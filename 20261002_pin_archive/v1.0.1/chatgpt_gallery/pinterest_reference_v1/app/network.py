"""Bounded public-image downloads; validate every redirect and pin DNS addresses."""
from __future__ import annotations
import http.client
import ipaddress
import socket
from urllib.parse import urlsplit, urljoin, quote
from .store import MAX_IMAGE_BYTES

class DownloadError(ValueError): pass

def validate_public_url(url):
    try:
        u=urlsplit(url)
        if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or any(c in url for c in '\r\n\0'):
            raise DownloadError('인증정보 없는 HTTP/HTTPS 이미지 주소만 지원합니다.')
        port=u.port or (443 if u.scheme=='https' else 80)
        if port not in (80,443): raise DownloadError('이미지 서버의 80/443 포트만 지원합니다.')
        ips=list(dict.fromkeys(r[4][0] for r in socket.getaddrinfo(u.hostname,port,type=socket.SOCK_STREAM)))
        if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
            raise DownloadError('로컬·사설·메타데이터 네트워크 주소는 허용하지 않습니다.')
        return u.hostname,port,ips[0]
    except (ValueError,OSError) as e:
        if isinstance(e,DownloadError): raise
        raise DownloadError('이미지 주소를 확인할 수 없습니다.') from e

def download_image(url,*,pinterest_only=False):
    for _ in range(5):
        u=urlsplit(url)
        if pinterest_only and not (u.hostname or '').endswith('.pinimg.com'):
            raise DownloadError('Pinterest 이미지 CDN 주소만 허용됩니다.')
        host,port,ip=validate_public_url(url)
        cls=http.client.HTTPSConnection if u.scheme=='https' else http.client.HTTPConnection
        conn=cls(host,port,timeout=20)
        # Preserve the original TLS SNI and Host while connecting to the vetted IP.
        conn._create_connection=lambda addr,timeout=20,source_address=None,**kw: socket.create_connection((ip,port),timeout,source_address)
        try:
            target=quote(u.path or '/',safe="/%:@!$&'()*+,;=-._~")+('?' + quote(u.query,safe="%=&/?+:,;@-._~") if u.query else '')
            conn.request('GET',target,headers={'User-Agent':'PinArchive/1.0 (personal reference library)','Accept':'image/*'})
            r=conn.getresponse()
            if r.status in (301,302,303,307,308):
                location=r.getheader('Location')
                if not location: raise DownloadError('리디렉션 주소가 없습니다.')
                url=urljoin(url,location); continue
            if r.status!=200: raise DownloadError(f'이미지 다운로드 HTTP {r.status}. 접근 제한은 우회하지 않습니다.')
            if r.getheader('Content-Length') and int(r.getheader('Content-Length'))>MAX_IMAGE_BYTES: raise DownloadError('파일이 20 MB를 초과합니다.')
            raw=r.read(MAX_IMAGE_BYTES+1)
            if len(raw)>MAX_IMAGE_BYTES: raise DownloadError('파일이 20 MB를 초과합니다.')
            return raw
        except (OSError,http.client.HTTPException) as e:
            raise DownloadError('이미지 연결 오류: '+type(e).__name__) from e
        finally: conn.close()
    raise DownloadError('리디렉션 횟수가 너무 많습니다.')
