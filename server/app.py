import logging
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import AppConfig, load_config
from .database import Database
from .handlers import LanGatewayHandler
from .image_cache import ImageCache
from .job_runner import JobRunner
from .logging_utils import configure_logging
from .upstream_client import UpstreamClient


class LanGatewayServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, server_address: tuple[str, int], handler_class: type[BaseHTTPRequestHandler], config: AppConfig, db: Database, runner: JobRunner, upstream_client: UpstreamClient, logger: logging.Logger, audit_logger: logging.Logger):
        super().__init__(server_address, handler_class)
        self.config = config
        self.db = db
        self.runner = runner
        self.upstream_client = upstream_client
        self.logger = logger
        self.audit_logger = audit_logger
        self.image_cache = runner.image_cache or ImageCache(config, logger)
        self.runner.image_cache = self.image_cache


def main() -> None:
    config = load_config()
    logger, audit_logger = configure_logging(config)
    config.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(config.data_dir / "jobs.sqlite")
    upstream_client = UpstreamClient(config)
    image_cache = ImageCache(config, logger)
    runner = JobRunner(config, db, logger, upstream_client, image_cache)
    server = LanGatewayServer((config.host, config.port), LanGatewayHandler, config, db, runner, upstream_client, logger, audit_logger)
    runner.start()
    logger.info("BizyAir LAN gateway running at http://%s:%s", config.host, config.port)
    logger.info(
        "Loaded config: port=%s worker_threads=%s log_level=%s openai_providers=%s openai_models=%s",
        config.port,
        config.worker_threads,
        config.log_level,
        list(config.openai_providers),
        [model for provider in config.openai_providers.values() for model in provider.models],
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("收到停止信号，正在退出")
    finally:
        runner.stop()
        server.server_close()
