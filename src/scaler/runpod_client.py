"""Thin RunPod REST client"""

from typing import Dict, List, Optional

import requests

BASE_URL = "https://api.runpod.io/v2"
GRAPHQL_URL = "https://api.runpod.io/graphql"
VOLUME_MOUNT_PATH = "/workspace"


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
                   network_volume_id: str, env: Dict[str, str], args: str,
                   cloud_type: str = "SECURE", gpu_count: int = 1,
                   allowed_cuda_versions: Optional[List[str]] = None) -> Dict:
        """Create a runpod pod with the defined parameters"""
        errors = []
        for gpu_type_id in gpu_type_ids:
            gpu = {"id": gpu_type_id, "count": gpu_count}
            if allowed_cuda_versions:
                gpu["allowedCudaVersions"] = list(allowed_cuda_versions)
            body = {
                "name": name,
                "templateId": template_id,
                "gpu": gpu,
                "mounts": {"network": [{"volumeId": network_volume_id,
                                        "path": VOLUME_MOUNT_PATH}]},
                "env": env,
                "args": args,
                "cloud": cloud_type,
            }
            try:
                return self._checked(
                    self.session.post(f"{self.base_url}/pods", json=body, timeout=60)).json()
            except RunPodError as e:
                errors.append(str(e))
        raise RunPodError(" | ".join(errors))

    def gpu_prices(self, cloud_type: str = "SECURE") -> Dict[str, float]:
        """RunPod's list price per hour for one card of each gpu type id"""
        field = "securePrice" if cloud_type == "SECURE" else "communityPrice"
        r = self._checked(self.session.post(
            GRAPHQL_URL, json={"query": f"query {{ gpuTypes {{ id {field} }} }}"}, timeout=30))
        body = r.json()
        if body.get("errors"):
            raise RunPodError(f"gpuTypes: {str(body['errors'])[:500]}")
        return {g["id"]: float(g[field]) for g in body["data"]["gpuTypes"]
                if g.get(field) is not None}

    def list_pods(self) -> List[Dict]:
        return self._checked(
            self.session.get(f"{self.base_url}/pods", timeout=30)).json()["pods"]

    def billing_pods(self, start_iso: str, end_iso: str, bucket: str = "day") -> List[Dict]:
        """Get runpod's billing results"""
        return self._checked(self.session.get(
            f"{self.base_url}/billing/pods",
            params={"startTime": start_iso, "endTime": end_iso, "bucketSize": bucket},
            timeout=30)).json()["records"]

    def terminate_pod(self, pod_id: str) -> None:
        r = self.session.delete(f"{self.base_url}/pods/{pod_id}", timeout=30)
        if r.status_code == 404:
            return
        self._checked(r)
