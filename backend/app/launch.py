"""Local production entry point; model downloads are explicit UI actions."""

import argparse
import errno
import socket
import threading
import time
import urllib.error
import urllib.request
import webbrowser


def launch_options(args=None):
    parser = argparse.ArgumentParser(description="启动本地视频文案提取器")
    parser.add_argument("--port", type=int, default=8765, help="本地端口，默认 8765")
    parser.add_argument(
        "--no-browser", dest="open_browser", action="store_false", help="只启动服务，不打开浏览器"
    )
    options = parser.parse_args(args)
    if not 1024 <= options.port <= 65535:
        parser.error("端口应在 1024 到 65535 之间")
    options.host = "127.0.0.1"
    return options


def _open_when_ready(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(100):
        try:
            with opener.open(url + "/api/session", timeout=1) as response:
                if response.status == 200:
                    webbrowser.open(url)
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.15)


def main(args=None):
    options = launch_options(args)
    import uvicorn

    with socket.socket() as probe:
        try:
            probe.bind((options.host, options.port))
        except PermissionError:
            raise SystemExit("没有绑定本机端口的权限，请检查运行环境或在普通终端启动。") from None
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise SystemExit("无法监听本机端口，请检查网络环境后重新启动。") from None
            raise SystemExit(
                f"端口 {options.port} 已被占用；若工具已启动，请打开 http://127.0.0.1:{options.port} 。"
            ) from None
    url = f"http://{options.host}:{options.port}"
    print(f"视频文案提取器：{url}\n关闭此终端或按 Ctrl+C 可停止服务。", flush=True)
    if options.open_browser:
        threading.Thread(target=_open_when_ready, args=(url,), daemon=True).start()
    uvicorn.run("app.main:app", host=options.host, port=options.port, access_log=False)


if __name__ == "__main__":
    main()
