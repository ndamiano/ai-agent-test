"""Thin EC2 client over the SigV4-signed Query API: priced offers, launch, list, terminate"""

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, List
from urllib.parse import urlencode
from xml.etree import ElementTree

import requests

API_VERSION = "2016-11-15"
PRICING_HOST = "api.pricing.us-east-1.amazonaws.com"
STOCK_CODES = frozenset({
    "InsufficientInstanceCapacity",
    "InsufficientHostCapacity",
    "MaxSpotInstanceCountExceeded",
    "SpotMaxPriceTooLow",
    "VcpuLimitExceeded",
    "Unsupported",
})


class Ec2Error(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code

    @property
    def stock(self) -> bool:
        return self.code in STOCK_CODES


@dataclass(frozen=True)
class Offer:
    region: str
    zone: str
    instance_type: str
    market: str
    usd_per_hour: float


@dataclass(frozen=True)
class Instance:
    id: str
    region: str
    zone: str
    name: str
    instance_type: str
    market: str
    launched_at: float


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sign(method: str, path: str, query: str, headers: Dict[str, str], body: bytes,
         region: str, service: str, access_key: str, secret_key: str) -> str:
    """The SigV4 Authorization value for a request whose headers carry host and x-amz-date"""
    canonical = sorted((k.lower(), " ".join(v.split())) for k, v in headers.items())
    signed = ";".join(k for k, _ in canonical)
    request = "\n".join([method, path, query,
                         "".join(f"{k}:{v}\n" for k, v in canonical), signed, _sha256(body)])
    amz_date = dict(canonical)["x-amz-date"]
    scope = f"{amz_date[:8]}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, _sha256(request.encode())])
    key = f"AWS4{secret_key}".encode()
    for part in scope.split("/"):
        key = _hmac(key, part)
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return (f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
            f"SignedHeaders={signed}, Signature={signature}")


def _strip_namespaces(root: ElementTree.Element) -> ElementTree.Element:
    for el in root.iter():
        el.tag = el.tag.rpartition("}")[2]
    return root


class Ec2Client:
    def __init__(self, access_key: str, secret_key: str):
        self.access_key = access_key
        self.secret_key = secret_key
        self.session = requests.Session()
        self._on_demand: Dict[tuple, float] = {}

    def _post(self, host: str, region: str, service: str, headers: Dict[str, str],
              body: bytes, timeout: int) -> requests.Response:
        headers = {**headers, "Host": host,
                   "X-Amz-Date": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}
        headers["Authorization"] = sign("POST", "/", "", headers, body, region, service,
                                        self.access_key, self.secret_key)
        return self.session.post(f"https://{host}/", data=body, headers=headers, timeout=timeout)

    def _call(self, region: str, action: str, params: Dict[str, str],
              timeout: int = 30) -> ElementTree.Element:
        body = urlencode({"Action": action, "Version": API_VERSION, **params}).encode()
        r = self._post(f"ec2.{region}.amazonaws.com", region, "ec2",
                       {"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
                       body, timeout)
        if not r.ok:
            try:
                error = _strip_namespaces(ElementTree.fromstring(r.text)).find(".//Error")
                code, message = error.findtext("Code"), error.findtext("Message")
            except (ElementTree.ParseError, AttributeError):
                code, message = str(r.status_code), r.text[:500]
            raise Ec2Error(code, f"{action} in {region}: {message}")
        return _strip_namespaces(ElementTree.fromstring(r.text))

    def zones(self, region: str, instance_type: str) -> List[str]:
        root = self._call(region, "DescribeInstanceTypeOfferings", {
            "LocationType": "availability-zone",
            "Filter.1.Name": "instance-type", "Filter.1.Value.1": instance_type})
        return sorted(el.text for el in root.iter("location"))

    def spot_prices(self, region: str, instance_type: str) -> Dict[str, float]:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        root = self._call(region, "DescribeSpotPriceHistory", {
            "InstanceType.1": instance_type, "ProductDescription.1": "Linux/UNIX",
            "StartTime": now})
        latest: Dict[str, tuple] = {}
        for item in root.iter("item"):
            zone, stamp = item.findtext("availabilityZone"), item.findtext("timestamp")
            if zone not in latest or stamp > latest[zone][0]:
                latest[zone] = (stamp, float(item.findtext("spotPrice")))
        return {zone: price for zone, (_, price) in latest.items()}

    def on_demand_price(self, region: str, instance_type: str) -> float:
        """The list price, asked once per process: it does not move with the market"""
        if (region, instance_type) in self._on_demand:
            return self._on_demand[(region, instance_type)]
        fields = {"instanceType": instance_type, "regionCode": region,
                  "operatingSystem": "Linux", "tenancy": "Shared",
                  "preInstalledSw": "NA", "capacitystatus": "Used"}
        body = json.dumps({
            "ServiceCode": "AmazonEC2",
            "Filters": [{"Type": "TERM_MATCH", "Field": k, "Value": v}
                        for k, v in fields.items()]}).encode()
        r = self._post(PRICING_HOST, "us-east-1", "pricing",
                       {"Content-Type": "application/x-amz-json-1.1",
                        "X-Amz-Target": "AWSPriceListService.GetProducts"}, body, 30)
        if not r.ok:
            raise Ec2Error(str(r.status_code), f"GetProducts: {r.text[:500]}")
        products = r.json()["PriceList"]
        if not products:
            raise Ec2Error("NoPrice", f"no on-demand price for {instance_type} in {region}")
        term = next(iter(json.loads(products[0])["terms"]["OnDemand"].values()))
        price = float(next(iter(term["priceDimensions"].values()))["pricePerUnit"]["USD"])
        self._on_demand[(region, instance_type)] = price
        return price

    def offers(self, regions: Iterable[str], instance_types: Iterable[str],
               markets: Iterable[str] = ("spot", "on-demand")) -> List[Offer]:
        """Cheapest first; a listed zone is not a stocked one, which only a launch answers"""
        markets = tuple(markets)
        out: List[Offer] = []
        for region in regions:
            for instance_type in instance_types:
                if "spot" in markets:
                    out += [Offer(region, zone, instance_type, "spot", price)
                            for zone, price in self.spot_prices(region, instance_type).items()]
                if "on-demand" in markets:
                    zones = self.zones(region, instance_type)
                    if zones:
                        price = self.on_demand_price(region, instance_type)
                        out += [Offer(region, zone, instance_type, "on-demand", price)
                                for zone in zones]
        return sorted(out, key=lambda o: (o.usd_per_hour, o.region, o.zone))

    def launch(self, offer: Offer, name: str, ami: str, env: Dict[str, str],
               security_group: str, instance_profile: str, root_gb: int = 100) -> str:
        """A zone with no subnet and a group by name resolve only in the default VPC"""
        params = {
            "ImageId": ami, "InstanceType": offer.instance_type,
            "MinCount": "1", "MaxCount": "1",
            "Placement.AvailabilityZone": offer.zone,
            "SecurityGroup.1": security_group,
            "IamInstanceProfile.Name": instance_profile,
            "InstanceInitiatedShutdownBehavior": "terminate",
            "BlockDeviceMapping.1.DeviceName": "/dev/sda1",
            "BlockDeviceMapping.1.Ebs.VolumeSize": str(root_gb),
            "BlockDeviceMapping.1.Ebs.VolumeType": "gp3",
            "BlockDeviceMapping.1.Ebs.DeleteOnTermination": "true",
            "MetadataOptions.HttpTokens": "required",
            "MetadataOptions.HttpPutResponseHopLimit": "1",
            "TagSpecification.1.ResourceType": "instance",
            "TagSpecification.1.Tag.1.Key": "Name",
            "TagSpecification.1.Tag.1.Value": name,
            "UserData": base64.b64encode(json.dumps(env).encode()).decode(),
        }
        if offer.market == "spot":
            params.update({
                "InstanceMarketOptions.MarketType": "spot",
                "InstanceMarketOptions.SpotOptions.SpotInstanceType": "one-time",
                "InstanceMarketOptions.SpotOptions.InstanceInterruptionBehavior": "terminate"})
        root = self._call(offer.region, "RunInstances", params, timeout=60)
        return root.find("instancesSet/item").findtext("instanceId")

    def list_instances(self, regions: Iterable[str], name_prefix: str) -> List[Instance]:
        out: List[Instance] = []
        for region in regions:
            root = self._call(region, "DescribeInstances", {
                "Filter.1.Name": "tag:Name", "Filter.1.Value.1": f"{name_prefix}*",
                "Filter.2.Name": "instance-state-name",
                "Filter.2.Value.1": "pending", "Filter.2.Value.2": "running"})
            for item in root.findall("reservationSet/item/instancesSet/item"):
                tags = {t.findtext("key"): t.findtext("value")
                        for t in item.findall("tagSet/item")}
                out.append(Instance(
                    id=item.findtext("instanceId"), region=region,
                    zone=item.findtext("placement/availabilityZone"), name=tags["Name"],
                    instance_type=item.findtext("instanceType"),
                    market=item.findtext("instanceLifecycle") or "on-demand",
                    launched_at=datetime.fromisoformat(
                        item.findtext("launchTime").replace("Z", "+00:00")).timestamp()))
        return out

    def terminate(self, region: str, instance_id: str) -> None:
        """The spot request goes first: a failed cancel must leave the instance listed"""
        requests_root = self._call(region, "DescribeSpotInstanceRequests", {
            "Filter.1.Name": "instance-id", "Filter.1.Value.1": instance_id,
            "Filter.2.Name": "state", "Filter.2.Value.1": "open", "Filter.2.Value.2": "active"})
        live = [el.text for el in requests_root.findall(
            "spotInstanceRequestSet/item/spotInstanceRequestId")]
        if live:
            self._call(region, "CancelSpotInstanceRequests", {
                f"SpotInstanceRequestId.{i}": request_id
                for i, request_id in enumerate(live, 1)})
        try:
            self._call(region, "TerminateInstances", {"InstanceId.1": instance_id})
        except Ec2Error as e:
            if e.code != "InvalidInstanceID.NotFound":
                raise
