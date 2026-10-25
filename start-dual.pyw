"""Windowed entry point; keep startup failures visible without a console."""
import ctypes
import sys

import remote


if __name__ == '__main__':
    try:
        remote.main()
    except Exception as error:
        try:
            remote.error_logger().exception('Application startup failed')
        except OSError:
            pass
        ctypes.windll.user32.MessageBoxW(
            None, str(error) + '\n\n详情见项目目录中的 remote-errors.log。',
            'Switch 2 启动失败', 0x10)
        sys.exit(1)
