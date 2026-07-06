import logging
import os
from dataclasses import dataclass, field
from typing import Optional

import requests

logger = logging.getLogger(__name__)


@dataclass
class ImgBBResult:
    url: str
    delete_url: str = ""
    thumb_url: Optional[str] = None
    display_url: Optional[str] = None
    raw: dict = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "display_url": self.display_url or self.url,
            "provider": "imgbb",
        }


class ImgBBUploader:
    API_URL = "https://api.imgbb.com/1/upload"

    def __init__(self, api_key: str, timeout: int = 30) -> None:
        if not api_key:
            raise ValueError("IMGBB_API_KEY 不能为空")
        self.api_key = api_key
        self.timeout = timeout

    def upload(self, image_path: str, name: Optional[str] = None) -> dict:
        image_path = os.path.abspath(image_path)
        if not os.path.isfile(image_path):
            raise FileNotFoundError(f"找不到图片: {image_path}")

        data: dict = {"key": self.api_key}
        if name:
            data["name"] = os.path.splitext(os.path.basename(name))[0]

        logger.info("上传文件至 ImgBB: %s", os.path.basename(image_path))
        with open(image_path, "rb") as file:
            response = requests.post(
                self.API_URL,
                data=data,
                files={"image": file},
                timeout=self.timeout,
            )
        return self._parse_response(response).to_dict()

    @staticmethod
    def _parse_response(response: requests.Response) -> ImgBBResult:
        if not response.ok:
            raise RuntimeError(f"ImgBB 上传失败: HTTP {response.status_code}")

        payload = response.json()
        if not payload.get("success"):
            message = payload.get("error", {}).get("message", "未知错误")
            raise RuntimeError(f"ImgBB API 错误: {message}")

        data = payload.get("data") or {}
        url = data.get("url") or data.get("display_url")
        if not url:
            raise RuntimeError("ImgBB 响应缺少图片 URL")
        return ImgBBResult(
            url=url,
            delete_url=data.get("delete_url", ""),
            thumb_url=(data.get("thumb") or {}).get("url"),
            display_url=data.get("display_url"),
            raw=payload,
        )
