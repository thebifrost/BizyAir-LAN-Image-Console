import logging
import os
from dataclasses import dataclass
from typing import Optional

import alibabacloud_oss_v2 as oss
import requests
from alibabacloud_oss_v2.credentials import StaticCredentialsProvider

logger = logging.getLogger(__name__)


@dataclass
class BizyFileInfo:
    access_key_id: str
    access_key_secret: str
    security_token: str
    object_key: str
    bucket: str
    region: str
    endpoint: str


class BizyUpImage:
    _TOKEN_URL = "https://api.bizyair.cn/x/v1/upload/token"
    _COMMIT_URL = "https://api.bizyair.cn/x/v1/input_resource/commit"
    _LIST_URL = "https://api.bizyair.cn/x/v1/input_resource"

    DEFAULT_BUCKET = "bizyair-prod"
    DEFAULT_REGION = "oss-cn-shanghai"

    def __init__(self, api_key: Optional[str] = None, timeout: int = 30) -> None:
        self.api_key = api_key or os.getenv("BIZYAIR_API_KEY") or os.getenv("APIKEY", "")
        if not self.api_key:
            raise ValueError("api_key 不能为空，请传入参数或设置环境变量 BIZYAIR_API_KEY")
        self.timeout = timeout

    def upload(self, file_path: str, file_type: str = "inputs", file_name: Optional[str] = None) -> dict:
        file_path = os.path.abspath(file_path)
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"找不到本地文件: {file_path}")

        file_name = file_name or os.path.basename(file_path)
        logger.info("请求 BizyAir 上传凭证: %s", file_name)
        file_info = self._get_token(file_name, file_type)

        logger.info("上传文件至 BizyAir OSS: bucket=%s", file_info.bucket)
        self._upload_oss(file_info, file_path)

        result = self._commit(file_name, file_info.object_key)
        logger.info("BizyAir 上传完成: %s", file_name)
        return result

    def list_inputs(self, current: int = 1, page_size: int = 20) -> dict:
        resp = requests.get(
            self._LIST_URL,
            headers=self._auth_headers(),
            params={"current": current, "page_size": page_size},
            timeout=self.timeout,
        )
        self._raise_for(resp, "查询 inputs 列表失败")
        return resp.json()

    def _get_token(self, file_name: str, file_type: str) -> BizyFileInfo:
        resp = requests.get(
            self._TOKEN_URL,
            headers=self._auth_headers(),
            params={"file_name": file_name, "file_type": file_type},
            timeout=self.timeout,
        )
        self._raise_for(resp, "获取上传凭证失败")

        token_data = resp.json()
        file_info = token_data.get("data", {}).get("file", {})
        if not file_info:
            raise RuntimeError("API 返回数据中未找到 file 信息")

        region = file_info.get("region", self.DEFAULT_REGION)
        return BizyFileInfo(
            access_key_id=file_info["access_key_id"],
            access_key_secret=file_info["access_key_secret"],
            security_token=file_info["security_token"],
            object_key=file_info["object_key"],
            bucket=file_info.get("bucket", self.DEFAULT_BUCKET),
            region=region,
            endpoint=file_info.get("endpoint", f"oss-{region.removeprefix('oss-')}.aliyuncs.com"),
        )

    def _upload_oss(self, info: BizyFileInfo, file_path: str) -> None:
        normalized_region = info.region.removeprefix("oss-")
        cfg = oss.config.Config(
            region=normalized_region,
            endpoint=info.endpoint,
            credentials_provider=StaticCredentialsProvider(
                access_key_id=info.access_key_id,
                access_key_secret=info.access_key_secret,
                security_token=info.security_token,
            ),
        )
        client = oss.Client(cfg)
        client.put_object_from_file(
            oss.PutObjectRequest(bucket=info.bucket, key=info.object_key),
            file_path,
        )

    def _commit(self, file_name: str, object_key: str) -> dict:
        resp = requests.post(
            self._COMMIT_URL,
            headers=self._auth_headers(),
            data={"name": file_name, "object_key": object_key},
            timeout=self.timeout,
        )
        self._raise_for(resp, "Commit 失败")
        return resp.json().get("data", {})

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    @staticmethod
    def _raise_for(resp: requests.Response, msg: str) -> None:
        if not resp.ok:
            raise RuntimeError(f"{msg}: HTTP {resp.status_code}")


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if len(sys.argv) < 2:
        raise SystemExit("用法: python back/bizyUpImage.py <image_path>")

    client = BizyUpImage()
    client.upload(sys.argv[1])
