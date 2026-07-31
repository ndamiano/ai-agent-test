"""Thin RunPod REST client — plain requests, no SDK.

The full worker env (CP_URL, WORKER_TOKEN, …) is passed at create time: correct whether
create-time env merges with or replaces the template's env (the one unverified RunPod detail —
see deploy.md for the one-time manual check).
"""

from typing import Dict, List

import requests

BASE_URL = "https://rest.runpod.io/v1"


class RunPodError(Exception):
    pass


class RunPodClient:
    def __init__(self, api_key: str, base_url: str = BASE_URL):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {api_key}"

    def _checked(self, r: requests.Response) -> requests.Response:
        if not r.ok:
            raise RunPodError(f"{r.request.method} {r.request.url} -> "
                              f"{r.status_code}: {r.text[:500]}")
        return r

    def create_pod(self, name: str, template_id: str, gpu_type_ids: List[str],
                   network_volume_id: str, env: Dict[str, str],
                   cloud_type: str = "SECURE", gpu_count: int = 1) -> Dict:
        body = {
            "name": name,
            "templateId": template_id,
            "gpuTypeIds": list(gpu_type_ids),
            "gpuCount": gpu_count,
            "networkVolumeId": network_volume_id,
            "env": env,
            "cloudType": cloud_type,
        }
        return self._checked(
            self.session.post(f"{self.base_url}/pods", json=body, timeout=60)).json()

    def list_pods(self) -> List[Dict]:
        return self._checked(
            self.session.get(f"{self.base_url}/pods", timeout=30)).json()

    def terminate_pod(self, pod_id: str) -> None:
        r = self.session.delete(f"{self.base_url}/pods/{pod_id}", timeout=30)
        if r.status_code == 404:
            return  # already gone — the outcome we wanted
        self._checked(r)
