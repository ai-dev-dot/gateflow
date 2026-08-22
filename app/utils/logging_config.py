"""统一 logging 配置（P2-8）。

- ``RequestIdFilter``: 从 ContextVar 读当前请求 id，注入每条 LogRecord--
  业务代码里的 ``logger.error(...)`` 零改动即自动携带 ``request_id``，
  与 ``X-Request-ID`` 响应头、audit log 里的 request_id 三方对齐。
- ``setup_logging(LOG_FORMAT)``:
  - ``text``（默认）: 控制台可读格式 ``[时间] LEVEL [request_id] logger: message``
  - ``json``: python-json-logger 单行 JSON（含 timestamp/level/logger/
    request_id/message），生产采集用。

uvicorn 自身的 access/error logger 保持默认（uvicorn 参数控制），这里
只配置应用侧 root logger--应用日志才是排障主力。
"""

import logging

from app.utils.request_id import current_request_id

# python-json-logger 3.x+ 提供两个导入路径，新旧版本兼容
try:
    from pythonjsonlogger.json import JsonFormatter
except ImportError:  # pragma: no cover - 旧版本回退
    from pythonjsonlogger.jsonlogger import JsonFormatter


class RequestIdFilter(logging.Filter):
    """Attach the current request id (or '-') to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id.get()
        return True


TEXT_FORMAT = "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"
JSON_FIELDS = ["timestamp", "level", "request_id", "name", "message"]


def setup_logging(log_format: str = "text") -> None:
    """(Re)configure the root logger. Call once at app startup."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # 清掉可能存在的旧 handler（测试里重复调用时不叠加）
    for handler in list(root.handlers):
        root.removeHandler(handler)

    if log_format == "json":
        formatter = JsonFormatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(request_id)s %(message)s",
            rename_fields={"asctime": "timestamp", "levelname": "level", "name": "logger"},
        )
    else:
        formatter = logging.Formatter(TEXT_FORMAT)

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    handler.addFilter(RequestIdFilter())
    root.addHandler(handler)

    # 应用噪音降级：httpx 在重试场景刷 INFO，只留 WARNING+
    logging.getLogger("httpx").setLevel(logging.WARNING)


__all__ = ["setup_logging", "RequestIdFilter"]
