"""Run a single local application; no LAN or public-internet binding."""
import argparse
from pathlib import Path
import socket
import threading
import time
import urllib.request
import uuid
import webbrowser

from app import __version__
from app.runtime import clear_runtime, data_root, discover_server, publish_runtime


def bind_available_socket(start_port: int, max_tries: int = 50):
    """Keep the selected socket open so another process cannot take its port."""
    for port in range(start_port, min(start_port + max_tries, 65536)):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(('127.0.0.1', port))
            sock.listen(2048)
            return sock, port
        except OSError:
            sock.close()
    raise RuntimeError(f'No free port found from {start_port} to {min(start_port + max_tries - 1, 65535)}')


def main():
    p = argparse.ArgumentParser(description=f'Pin Archive {__version__} local server')
    p.add_argument('--port', type=int, default=8765)
    p.add_argument('--no-browser', action='store_true')
    p.add_argument('--no-seed', action='store_true')
    p.add_argument('--data-dir', type=Path)
    args = p.parse_args()
    if not 1024 <= args.port <= 65535:
        p.error('port must be 1024..65535')
    try:
        import uvicorn
        from app.main import create_app
    except ImportError as e:
        print('Run 01_setup.bat first. Missing dependency:', e)
        return 1
    root = data_root(args.data_dir)
    try:
        active = discover_server(root)
    except RuntimeError:
        pass
    else:
        print(f'Pin Archive is already running with this data folder at {active}.')
        return 1
    try:
        sock, port = bind_available_socket(args.port)
    except RuntimeError as e:
        print(e)
        return 1
    instance_id = uuid.uuid4().hex
    try:
        app = create_app(args.data_dir, seed=not args.no_seed, instance_id=instance_id)
        url = f'http://127.0.0.1:{port}'
        publish_runtime(root, port, instance_id)
        if port != args.port:
            print(f'Port {args.port} is already in use. Using port {port} instead.')
        if not args.no_browser:
            def open_ready():
                for _ in range(80):
                    try:
                        with urllib.request.urlopen(url + '/api/health', timeout=1) as response:
                            if response.status == 200:
                                webbrowser.open(url)
                                return
                    except Exception:
                        time.sleep(.25)
            threading.Thread(target=open_ready, daemon=True).start()
        print(f'\nPin Archive {__version__}\n{url}\nData: {app.state.store.root}\nPress Ctrl+C to stop.\n')
        config = uvicorn.Config(app, host='127.0.0.1', port=port, log_level='info')
        try:
            uvicorn.Server(config).run(sockets=[sock])
        except KeyboardInterrupt:
            pass
        return 0
    finally:
        clear_runtime(data_root(args.data_dir), instance_id)
        sock.close()


if __name__ == '__main__':
    raise SystemExit(main())
