"""Read-only folder extraction, local vector index, and source retrieval."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import sqlite3
from array import array
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from docx import Document
from pypdf import PdfReader

from .config import DATA_DIR, EMBEDDING_MODEL
from .ollama_client import embed


TEXT_EXTENSIONS = {
    ".txt", ".md", ".rst", ".py", ".js", ".jsx", ".ts", ".tsx",
    ".json", ".csv", ".html", ".htm", ".css", ".java", ".c",
    ".cpp", ".h", ".hpp", ".ps1", ".yaml", ".yml", ".toml",
    ".ini", ".log",
}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | {".pdf", ".docx"}
SKIP_DIRECTORIES = {
    ".git", ".venv", "node_modules", "__pycache__", ".next",
    "dist", "build", ".idea", ".vs", "System Volume Information",
}
MAX_FILES = 200
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_CHUNKS = 600
MAX_TOTAL_CHARS = 600_000
CHUNK_CHARS = 1100
CHUNK_OVERLAP = 150


@dataclass
class Chunk:
    source: str
    locator: str
    text: str


class IndexErrorMessage(RuntimeError):
    pass


def resolve_folder(raw: str) -> Path:
    if not raw.strip():
        raise IndexErrorMessage("폴더 경로를 입력해 주세요.")
    folder = Path(raw.strip().strip('"')).expanduser().resolve(strict=False)
    if not folder.is_dir():
        raise IndexErrorMessage("존재하는 폴더 경로를 입력해 주세요.")
    return folder


def index_path(folder: Path) -> Path:
    key = hashlib.sha256(str(folder).casefold().encode("utf-8")).hexdigest()[:20]
    return DATA_DIR / "indexes" / f"{key}.sqlite3"


def _split_section(source: str, locator: str, text: str, *, line_mode: bool = False) -> list[Chunk]:
    text = text.replace("\x00", "").replace("\r\n", "\n")
    if not text.strip():
        return []
    pieces: list[Chunk] = []
    start = 0
    while start < len(text):
        end = min(start + CHUNK_CHARS, len(text))
        if end < len(text):
            boundary = text.rfind("\n", start + CHUNK_CHARS // 2, end)
            if boundary > start:
                end = boundary + 1
        segment = text[start:end].strip()
        if segment:
            if line_mode:
                first_line = text.count("\n", 0, start) + 1
                last_line = text.count("\n", 0, max(start, end - 1)) + 1
                position = f"L{first_line}-{last_line}"
            else:
                position = locator
            pieces.append(Chunk(source, position, segment))
        if end >= len(text):
            break
        start = max(start + 1, end - CHUNK_OVERLAP)
    return pieces


def _file_chunks(path: Path, root: Path) -> list[Chunk]:
    source = path.relative_to(root).as_posix()
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        content = path.read_bytes()
        if b"\x00" in content[:4096]:
            raise IndexErrorMessage("텍스트 파일에서 바이너리 데이터를 발견했습니다.")
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("cp949")
        return _split_section(source, "", text, line_mode=True)

    if suffix == ".pdf":
        reader = PdfReader(str(path), strict=False)
        sections: list[Chunk] = []
        for number, page in enumerate(reader.pages[:100], 1):
            extracted = page.extract_text() or ""
            sections.extend(_split_section(source, f"p.{number}", extracted))
        return sections

    if suffix == ".docx":
        document = Document(str(path))
        sections: list[Chunk] = []
        paragraph_group: list[str] = []
        first = 0
        for number, paragraph in enumerate(document.paragraphs, 1):
            text = paragraph.text.strip()
            if not text:
                continue
            if not paragraph_group:
                first = number
            if paragraph_group and sum(map(len, paragraph_group)) + len(text) > CHUNK_CHARS:
                sections.extend(_split_section(source, f"문단 {first}-{number - 1}", "\n".join(paragraph_group)))
                paragraph_group = []
                first = number
            paragraph_group.append(text)
        if paragraph_group:
            sections.extend(_split_section(source, f"문단 {first}-{number}", "\n".join(paragraph_group)))
        return sections
    return []


def collect_chunks(folder: Path) -> tuple[list[Chunk], dict]:
    chunks: list[Chunk] = []
    skipped: list[str] = []
    indexed_files = 0
    scanned_files = 0
    total_chars = 0
    stop = False

    for parent, dirs, files in os.walk(folder, followlinks=False):
        dirs[:] = sorted(
            name for name in dirs
            if name not in SKIP_DIRECTORIES and not name.startswith(".")
            and not (Path(parent) / name).is_symlink()
        )
        for name in sorted(files):
            path = Path(parent) / name
            if name.startswith(".") or path.is_symlink() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            scanned_files += 1
            if scanned_files > MAX_FILES:
                skipped.append(f"파일 수 제한({MAX_FILES}개)에 도달했습니다.")
                stop = True
                break
            relative = path.relative_to(folder).as_posix()
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    skipped.append(f"{relative}: 5MB 초과")
                    continue
                extracted = _file_chunks(path, folder)
                if not extracted:
                    skipped.append(f"{relative}: 추출 가능한 텍스트 없음")
                    continue
                proposed_chars = sum(len(chunk.text) for chunk in extracted)
                if len(chunks) + len(extracted) > MAX_CHUNKS or total_chars + proposed_chars > MAX_TOTAL_CHARS:
                    skipped.append("색인 크기 제한에 도달했습니다. 더 작은 폴더를 선택해 주세요.")
                    stop = True
                    break
                chunks.extend(extracted)
                total_chars += proposed_chars
                indexed_files += 1
            except Exception as exc:  # A single malformed source must not abort the whole folder.
                skipped.append(f"{relative}: {type(exc).__name__}")
        if stop:
            break

    if not chunks:
        raise IndexErrorMessage("읽을 수 있는 문서를 찾지 못했습니다. TXT·MD·코드·PDF·DOCX 파일을 확인해 주세요.")
    return chunks, {
        "folder": str(folder),
        "indexed_files": indexed_files,
        "scanned_files": min(scanned_files, MAX_FILES),
        "chunks": len(chunks),
        "skipped": skipped[:30],
        "embedding_model": EMBEDDING_MODEL,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _save_index(folder: Path, chunks: list[Chunk], vectors: list[list[float]], meta: dict) -> None:
    destination = index_path(folder)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".building")
    if temporary.exists():
        temporary.unlink()
    try:
        with closing(sqlite3.connect(temporary)) as connection, connection:
            connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute(
                "CREATE TABLE chunks (id INTEGER PRIMARY KEY, source TEXT, locator TEXT, content TEXT, vector BLOB)"
            )
            connection.execute("INSERT INTO meta VALUES (?, ?)", ("summary", json.dumps(meta, ensure_ascii=False)))
            connection.executemany(
                "INSERT INTO chunks (source, locator, content, vector) VALUES (?, ?, ?, ?)",
                (
                    (
                        chunk.source,
                        chunk.locator,
                        chunk.text,
                        array("f", vector).tobytes(),
                    )
                    for chunk, vector in zip(chunks, vectors, strict=True)
                )
            )
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


async def build_index(folder: Path, progress: Callable[[str, int, int], None]) -> dict:
    progress("문서를 읽는 중", 0, 0)
    chunks, meta = await asyncio.to_thread(collect_chunks, folder)
    progress("문서 추출 완료", len(chunks), len(chunks))
    vectors: list[list[float]] = []
    batch_size = 12
    for start in range(0, len(chunks), batch_size):
        progress("검색용 벡터 생성 중", start, len(chunks))
        batch = chunks[start : start + batch_size]
        vectors.extend(
            await embed([chunk.text for chunk in batch], keep_alive=0 if start + batch_size >= len(chunks) else "5m")
        )
        progress("검색용 벡터 생성 중", len(vectors), len(chunks))
    progress("저장 중", len(chunks), len(chunks))
    await asyncio.to_thread(_save_index, folder, chunks, vectors, meta)
    return meta


def get_index_summary(folder: Path) -> dict | None:
    path = index_path(folder)
    if not path.exists():
        return None
    with closing(sqlite3.connect(path)) as connection:
        row = connection.execute("SELECT value FROM meta WHERE key='summary'").fetchone()
    if not row:
        return None
    summary = json.loads(row[0])
    return summary if summary.get("folder") == str(folder) else None


def _load_chunks(folder: Path) -> list[tuple[Chunk, list[float]]]:
    path = index_path(folder)
    if not path.exists():
        raise IndexErrorMessage("이 폴더의 색인이 없습니다. 먼저 색인을 생성해 주세요.")
    with closing(sqlite3.connect(path)) as connection:
        meta = connection.execute("SELECT value FROM meta WHERE key='summary'").fetchone()
        if not meta or json.loads(meta[0]).get("folder") != str(folder):
            raise IndexErrorMessage("색인 폴더가 현재 폴더와 일치하지 않습니다.")
        rows = connection.execute("SELECT source, locator, content, vector FROM chunks").fetchall()
    result = []
    for source, locator, content, blob in rows:
        vector = array("f")
        vector.frombytes(blob)
        result.append((Chunk(source, locator, content), list(vector)))
    return result


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return -1
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    return dot / (left_norm * right_norm) if left_norm and right_norm else -1


def search_chunks(folder: Path, question: str, query_vector: list[float], limit: int = 5) -> list[dict]:
    terms = [term.casefold() for term in re.findall(r"[\w가-힣]{2,}", question)]
    ranked = []
    for chunk, vector in _load_chunks(folder):
        semantic = _cosine(query_vector, vector)
        lowered = chunk.text.casefold()
        keyword_hits = sum(term in lowered for term in terms)
        score = semantic + min(keyword_hits, 3) * 0.025
        ranked.append((score, semantic, chunk))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [
        {"id": index, "source": chunk.source, "locator": chunk.locator,
         "excerpt": chunk.text[:1200], "score": round(semantic, 3)}
        for index, (_, semantic, chunk) in enumerate(ranked[:limit], 1)
    ]

