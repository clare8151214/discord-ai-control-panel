from __future__ import annotations

import asyncio
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import aiohttp


class ImageGenerationError(RuntimeError):
    """ComfyUI API 或工作流程錯誤。"""


@dataclass(frozen=True, slots=True)
class GeneratedImage:
    content: bytes
    filename: str
    seed: int


class ComfyUIImageClient:
    def __init__(
        self,
        base_url: str,
        checkpoint: str,
        width: int,
        height: int,
        steps: int,
        cfg: float,
        timeout_seconds: int,
        ipadapter_model: str = "",
        clip_vision_model: str = "",
        ipadapter_end_at: float = 0.8,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.checkpoint = checkpoint
        self.width = width
        self.height = height
        self.steps = steps
        self.cfg = cfg
        self.timeout_seconds = timeout_seconds
        self.ipadapter_model = ipadapter_model
        self.clip_vision_model = clip_vision_model
        self.ipadapter_end_at = ipadapter_end_at
        self.client_id = str(uuid.uuid4())
        self.session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        timeout = aiohttp.ClientTimeout(
            total=self.timeout_seconds,
            connect=10,
        )
        self.session = aiohttp.ClientSession(timeout=timeout)

    async def close(self) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None

    def _session(self) -> aiohttp.ClientSession:
        if self.session is None:
            raise ImageGenerationError("ComfyUI HTTP client 尚未啟動")
        return self.session

    async def health(self) -> dict[str, Any]:
        try:
            async with self._session().get(
                f"{self.base_url}/system_stats"
            ) as response:
                if response.status != 200:
                    raise ImageGenerationError(
                        f"ComfyUI health check HTTP {response.status}"
                    )
                payload = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise ImageGenerationError(
                f"無法連線至 ComfyUI：{self.base_url}"
            ) from exc
        return payload

    async def node_info(self, node_name: str) -> dict[str, Any]:
        """確認 ComfyUI 工作流程所需節點已載入。"""
        try:
            async with self._session().get(
                f"{self.base_url}/object_info/{node_name}"
            ) as response:
                if response.status != 200:
                    raise ImageGenerationError(
                        f"ComfyUI node check HTTP {response.status}: {node_name}"
                    )
                payload = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise ImageGenerationError(
                f"無法檢查 ComfyUI 節點：{node_name}"
            ) from exc
        node = payload.get(node_name)
        if not isinstance(node, dict):
            raise ImageGenerationError(f"ComfyUI 缺少必要節點：{node_name}")
        return node

    def _workflow(
        self,
        positive: str,
        negative: str,
        seed: int,
        *,
        reference_image_name: str | None = None,
        reference_weight: float = 0.65,
    ) -> dict[str, Any]:
        workflow: dict[str, Any] = {
            "3": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": seed,
                    "steps": self.steps,
                    "cfg": self.cfg,
                    "sampler_name": "euler_ancestral",
                    "scheduler": "normal",
                    "denoise": 1.0,
                    "model": ["4", 0],
                    "positive": ["6", 0],
                    "negative": ["7", 0],
                    "latent_image": ["5", 0],
                },
            },
            "4": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": self.checkpoint},
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {
                    "width": self.width,
                    "height": self.height,
                    "batch_size": 1,
                },
            },
            "6": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": positive, "clip": ["4", 1]},
            },
            "7": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": negative, "clip": ["4", 1]},
            },
            "8": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
            },
            "9": {
                "class_type": "PreviewImage",
                "inputs": {"images": ["8", 0]},
            },
        }
        if reference_image_name is not None:
            if not self.ipadapter_model or not self.clip_vision_model:
                raise ImageGenerationError("IP-Adapter 或 CLIP Vision 模型尚未設定")
            workflow.update(
                {
                    "10": {
                        "class_type": "LoadImage",
                        "inputs": {"image": reference_image_name},
                    },
                    "11": {
                        "class_type": "CLIPVisionLoader",
                        "inputs": {"clip_name": self.clip_vision_model},
                    },
                    "12": {
                        "class_type": "IPAdapterModelLoader",
                        "inputs": {"ipadapter_file": self.ipadapter_model},
                    },
                    "13": {
                        "class_type": "IPAdapterAdvanced",
                        "inputs": {
                            "model": ["4", 0],
                            "ipadapter": ["12", 0],
                            "image": ["10", 0],
                            "weight": reference_weight,
                            "weight_type": "linear",
                            "combine_embeds": "concat",
                            "start_at": 0.0,
                            "end_at": self.ipadapter_end_at,
                            "embeds_scaling": "V only",
                            "clip_vision": ["11", 0],
                        },
                    },
                }
            )
            workflow["3"]["inputs"]["model"] = ["13", 0]
        return workflow

    async def _upload_reference(self, content: bytes, filename: str) -> str:
        form = aiohttp.FormData()
        form.add_field(
            "image",
            content,
            filename=filename,
            content_type="application/octet-stream",
        )
        form.add_field("type", "input")
        form.add_field("subfolder", "discord_character_refs")
        form.add_field("overwrite", "true")
        try:
            async with self._session().post(
                f"{self.base_url}/upload/image",
                data=form,
            ) as response:
                payload = await response.json(content_type=None)
                if response.status != 200:
                    raise ImageGenerationError(
                        f"ComfyUI reference upload HTTP {response.status}: {str(payload)[:500]}"
                    )
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise ImageGenerationError("上傳角色基準圖至 ComfyUI 失敗") from exc

        name = str(payload.get("name", ""))
        subfolder = str(payload.get("subfolder", "")).strip("/\\")
        if not name:
            raise ImageGenerationError(f"ComfyUI 未回傳基準圖檔名：{payload}")
        return f"{subfolder}/{name}" if subfolder else name

    async def _queue(self, workflow: dict[str, Any]) -> str:
        body = {"prompt": workflow, "client_id": self.client_id}
        try:
            async with self._session().post(
                f"{self.base_url}/prompt",
                json=body,
            ) as response:
                payload = await response.json(content_type=None)
                if response.status != 200:
                    raise ImageGenerationError(
                        f"ComfyUI queue HTTP {response.status}: {str(payload)[:500]}"
                    )
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise ImageGenerationError("送出 ComfyUI 工作流程失敗") from exc
        prompt_id = str(payload.get("prompt_id", ""))
        if not prompt_id:
            raise ImageGenerationError(f"ComfyUI 未回傳 prompt_id：{payload}")
        return prompt_id

    async def _wait_for_image(self, prompt_id: str) -> dict[str, str]:
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            try:
                async with self._session().get(
                    f"{self.base_url}/history/{prompt_id}"
                ) as response:
                    if response.status != 200:
                        raise ImageGenerationError(
                            f"ComfyUI history HTTP {response.status}"
                        )
                    history = await response.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                raise ImageGenerationError("查詢 ComfyUI 生成進度失敗") from exc

            record = history.get(prompt_id)
            if record:
                status = record.get("status", {})
                if status.get("status_str") == "error":
                    messages = status.get("messages", [])
                    raise ImageGenerationError(
                        f"ComfyUI 工作流程失敗：{str(messages)[-800:]}"
                    )
                for output in record.get("outputs", {}).values():
                    images = output.get("images", [])
                    if images:
                        image = images[0]
                        return {
                            "filename": str(image.get("filename", "")),
                            "subfolder": str(image.get("subfolder", "")),
                            "type": str(image.get("type", "temp")),
                        }
            await asyncio.sleep(1)
        raise ImageGenerationError(
            f"ComfyUI 生成超過 {self.timeout_seconds} 秒"
        )

    async def _download(self, image: dict[str, str]) -> bytes:
        query = urlencode(image)
        try:
            async with self._session().get(
                f"{self.base_url}/view?{query}"
            ) as response:
                if response.status != 200:
                    raise ImageGenerationError(
                        f"ComfyUI image HTTP {response.status}"
                    )
                return await response.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise ImageGenerationError("下載 ComfyUI 圖片失敗") from exc

    async def generate(
        self,
        positive: str,
        negative: str,
        *,
        reference_image: bytes | None = None,
        reference_filename: str = "character.png",
        reference_weight: float = 0.65,
    ) -> GeneratedImage:
        seed = secrets.randbelow(2**63 - 1)
        reference_image_name = None
        if reference_image is not None:
            reference_image_name = await self._upload_reference(
                reference_image,
                reference_filename,
            )
        prompt_id = await self._queue(
            self._workflow(
                positive,
                negative,
                seed,
                reference_image_name=reference_image_name,
                reference_weight=reference_weight,
            )
        )
        image_info = await self._wait_for_image(prompt_id)
        content = await self._download(image_info)
        if not content:
            raise ImageGenerationError("ComfyUI 回傳空白圖片")
        filename = image_info["filename"] or f"scene-{prompt_id}.png"
        return GeneratedImage(content=content, filename=filename, seed=seed)
