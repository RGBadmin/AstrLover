"""云 ComfyUI 后端：workflow API 模板方式。

用户提供导出的 workflow（API 格式 JSON）放在插件数据目录，
其中以 "{POSITIVE}" / "{NEGATIVE}" / "{SEED}" / "{WIDTH}" / "{HEIGHT}" 占位；
人物一致性建议在 workflow 内部用 LoRA / IPAdapter / InstantID 保证。
"""

import asyncio
import json
import random
from pathlib import Path

import aiohttp

from .base import ImageBackend
from .prompt_builder import PromptSpec


class ComfyUIBackend(ImageBackend):
    name = "comfyui"
    data_dir: Path | None = None  # 由 ImageGen 注入

    def configured(self) -> bool:
        return bool(self.conf.get("base_url"))

    def _headers(self) -> dict:
        key = self.conf.get("api_key")
        return {"Authorization": f"Bearer {key}"} if key else {}

    def _load_workflow(self, spec: PromptSpec) -> dict:
        wf_name = str(self.conf.get("workflow_file") or "comfyui_workflow.json")
        wf_path = Path(wf_name)
        if not wf_path.is_absolute() and self.data_dir is not None:
            wf_path = self.data_dir / wf_name
        raw = wf_path.read_text(encoding="utf-8")
        raw = raw.replace("{POSITIVE}", json.dumps(spec.positive, ensure_ascii=False)[1:-1])
        raw = raw.replace("{NEGATIVE}", json.dumps(spec.negative, ensure_ascii=False)[1:-1])
        raw = raw.replace('"{WIDTH}"', str(spec.width))
        raw = raw.replace('"{HEIGHT}"', str(spec.height))
        raw = raw.replace("{WIDTH}", str(spec.width))
        raw = raw.replace("{HEIGHT}", str(spec.height))
        raw = raw.replace('"{SEED}"', str(random.randint(0, 2**31 - 1)))
        raw = raw.replace("{SEED}", str(random.randint(0, 2**31 - 1)))
        return json.loads(raw)

    async def generate(self, spec: PromptSpec) -> bytes:
        base = str(self.conf.get("base_url")).rstrip("/")
        workflow = self._load_workflow(spec)
        timeout = aiohttp.ClientTimeout(total=300)
        async with aiohttp.ClientSession(timeout=timeout, headers=self._headers()) as session:
            async with session.post(f"{base}/prompt", json={"prompt": workflow}) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"提交失败 HTTP {resp.status}: {(await resp.text())[:200]}")
                prompt_id = (await resp.json()).get("prompt_id")
            if not prompt_id:
                raise RuntimeError("未返回 prompt_id")

            # 轮询执行结果
            for _ in range(150):
                await asyncio.sleep(2)
                async with session.get(f"{base}/history/{prompt_id}") as resp:
                    if resp.status != 200:
                        continue
                    history = await resp.json()
                entry = history.get(prompt_id)
                if not entry:
                    continue
                images = []
                for node_output in (entry.get("outputs") or {}).values():
                    images.extend(node_output.get("images") or [])
                if not images:
                    if entry.get("status", {}).get("completed"):
                        raise RuntimeError("workflow 完成但没有图片输出")
                    continue
                img = images[0]
                params = {
                    "filename": img.get("filename", ""),
                    "subfolder": img.get("subfolder", ""),
                    "type": img.get("type", "output"),
                }
                async with session.get(f"{base}/view", params=params) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"取图失败 HTTP {resp.status}")
                    return await resp.read()
            raise RuntimeError("等待超时")
