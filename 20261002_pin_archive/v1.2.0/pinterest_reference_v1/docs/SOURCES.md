# 참고한 공식 문서

확인일: 2026-10-02. 아래는 인터페이스와 이용 조건을 확인한 자료입니다. 문서를 확인했다는 사실이 실제 계정·모델과의 연동 성공을 의미하지는 않습니다.

| 주제 | 공식 출처 | 적용 내용 |
|---|---|---|
| Pinterest 이용약관 | https://policy.pinterest.com/en/terms-of-service | 허가 없는 자동 접근을 기본 지원으로 가정하지 않음 |
| Playwright persistent context | https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context | 전용 프로필, 브라우저 종료/충돌 처리 |
| OpenAI 이미지 입력 | https://developers.openai.com/api/docs/guides/images-vision | Responses input_image/base64 이미지 |
| OpenAI 구조화 출력 | https://developers.openai.com/api/docs/guides/structured-outputs | JSON Schema 형식과 Pydantic 검증 |
| OpenAI 과금 구분 | https://help.openai.com/en/articles/9039756-billing-settings-in-chatgpt-vs-platform | API 사용과 ChatGPT 구독을 구분 |
| ChatGPT MCP 연결 | https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt-beta | localhost 직접 연결과 원격/터널 구성을 구분 |
| Ollama Chat API | https://docs.ollama.com/api/chat | images, format, stream=false |
| Sentence Transformers 이미지 검색 | https://sbert.net/examples/sentence_transformer/applications/image-search/README.html | 이미지 CLIP과 다국어 텍스트 모델 조합 |
| MCP stdio 전송 | https://modelcontextprotocol.io/specification/2025-06-18/basic/transports | 개행 구분 JSON-RPC, stdout 프로토콜 전용 |
| MCP 도구 | https://modelcontextprotocol.io/specification/2025-06-18/server/tools | 읽기 전용 도구 정의와 image/text content |
