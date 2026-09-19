"""EC2 client: SigV4 against AWS's published vector, priced offers, launch params, refusals."""

import base64
import json
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import parse_qs

import pytest

from scaler.ec2_client import Ec2Client, Ec2Error, Offer, sign

NS = 'xmlns="http://ec2.amazonaws.com/doc/2016-11-15/"'


def _resp(status=200, text="", body=None):
    return SimpleNamespace(ok=status < 400, status_code=status, text=text, json=lambda: body)


def _error(code, message="no"):
    return _resp(400, f"<Response><Errors><Error><Code>{code}</Code><Message>{message}"
                      f"</Message></Error></Errors><RequestID>r</RequestID></Response>")


def _spot(*rows):
    items = "".join(f"<item><instanceType>g7e.2xlarge</instanceType><spotPrice>{price}"
                    f"</spotPrice><timestamp>{stamp}</timestamp><availabilityZone>{zone}"
                    f"</availabilityZone></item>" for zone, price, stamp in rows)
    return _resp(text=f"<DescribeSpotPriceHistoryResponse {NS}><spotPriceHistorySet>{items}"
                      f"</spotPriceHistorySet></DescribeSpotPriceHistoryResponse>")


def _offerings(*zones):
    items = "".join(f"<item><instanceType>g7e.2xlarge</instanceType><location>{z}</location>"
                    f"</item>" for z in zones)
    return _resp(text=f"<DescribeInstanceTypeOfferingsResponse {NS}><instanceTypeOfferingSet>"
                      f"{items}</instanceTypeOfferingSet></DescribeInstanceTypeOfferingsResponse>")


def _price(usd):
    product = {"terms": {"OnDemand": {"sku.term": {"priceDimensions": {
        "sku.term.dim": {"pricePerUnit": {"USD": usd}}}}}}}
    return _resp(body={"PriceList": [json.dumps(product)]})


def _client(*responses):
    c = Ec2Client("AKID", "secret")
    c.session = MagicMock()
    c.session.post.side_effect = list(responses)
    return c


def _sent(c, i=0):
    call = c.session.post.call_args_list[i]
    return call.args[0], {k: v[0] for k, v in parse_qs(call.kwargs["data"].decode()).items()}


def test_sign_matches_the_aws_sigv4_suite_get_vanilla_vector():
    auth = sign("GET", "/", "", {"Host": "example.amazonaws.com",
                                 "X-Amz-Date": "20150830T123600Z"}, b"",
                "us-east-1", "service", "AKIDEXAMPLE",
                "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY")
    assert auth == ("AWS4-HMAC-SHA256 "
                    "Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request, "
                    "SignedHeaders=host;x-amz-date, Signature="
                    "5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31")


def test_offers_price_every_listed_zone_in_both_markets_cheapest_first():
    c = _client(
        _spot(("eu-north-1a", "1.16", "2026-09-18T10:00:00.000Z"),
              ("eu-north-1a", "9.99", "2026-09-18T08:00:00.000Z"),
              ("eu-north-1b", "2.40", "2026-09-18T09:00:00.000Z")),
        _offerings("eu-north-1b", "eu-north-1a"), _price("3.36"),
        _spot(("us-west-2a", "1.25", "2026-09-18T10:00:00.000Z")),
        _offerings("us-west-2a"), _price("3.10"))
    offers = c.offers(["eu-north-1", "us-west-2"], ["g7e.2xlarge"])
    assert [(o.zone, o.market, o.usd_per_hour) for o in offers] == [
        ("eu-north-1a", "spot", 1.16), ("us-west-2a", "spot", 1.25),
        ("eu-north-1b", "spot", 2.40), ("us-west-2a", "on-demand", 3.10),
        ("eu-north-1a", "on-demand", 3.36), ("eu-north-1b", "on-demand", 3.36)]
    url, sent = _sent(c, 3)
    assert url == "https://ec2.us-west-2.amazonaws.com/"
    assert sent["Action"] == "DescribeSpotPriceHistory"
    pricing = c.session.post.call_args_list[2]
    assert pricing.args[0] == "https://api.pricing.us-east-1.amazonaws.com/"
    assert "/us-east-1/pricing/aws4_request" in pricing.kwargs["headers"]["Authorization"]
    assert {"Type": "TERM_MATCH", "Field": "regionCode", "Value": "eu-north-1"} in \
        json.loads(pricing.kwargs["data"])["Filters"]


def test_offers_spot_only_asks_nothing_about_on_demand():
    c = _client(_spot(("eu-north-1a", "1.16", "2026-09-18T10:00:00.000Z")))
    assert c.offers(["eu-north-1"], ["g7e.2xlarge"], markets=["spot"]) == [
        Offer("eu-north-1", "eu-north-1a", "g7e.2xlarge", "spot", 1.16)]
    assert c.session.post.call_count == 1


@pytest.mark.parametrize("market", ["spot", "on-demand"])
def test_launch_sends_the_box_the_image_expects_and_returns_its_id(market):
    c = _client(_resp(text=f"<RunInstancesResponse {NS}><instancesSet><item><instanceId>i-0abc"
                           f"</instanceId></item></instancesSet></RunInstancesResponse>"))
    env = {"CP_URL": "https://cp", "WORKER_ID": "w1"}
    offer = Offer("eu-north-1", "eu-north-1a", "g7e.2xlarge", market, 1.16)
    assert c.launch(offer, "gs-llm-a1", "ami-1", env, "gs-gpu-worker", "gs-gpu-worker") == "i-0abc"
    url, sent = _sent(c)
    assert url == "https://ec2.eu-north-1.amazonaws.com/"
    assert json.loads(base64.b64decode(sent.pop("UserData"))) == env
    spot = {k: sent.pop(k) for k in list(sent) if k.startswith("InstanceMarketOptions.")}
    assert spot == ({"InstanceMarketOptions.MarketType": "spot",
                     "InstanceMarketOptions.SpotOptions.SpotInstanceType": "one-time",
                     "InstanceMarketOptions.SpotOptions.InstanceInterruptionBehavior":
                         "terminate"} if market == "spot" else {})
    assert sent == {
        "Action": "RunInstances", "Version": "2016-11-15",
        "ImageId": "ami-1", "InstanceType": "g7e.2xlarge", "MinCount": "1", "MaxCount": "1",
        "Placement.AvailabilityZone": "eu-north-1a", "SecurityGroup.1": "gs-gpu-worker",
        "IamInstanceProfile.Name": "gs-gpu-worker",
        "InstanceInitiatedShutdownBehavior": "terminate",
        "BlockDeviceMapping.1.DeviceName": "/dev/sda1",
        "BlockDeviceMapping.1.Ebs.VolumeSize": "100",
        "BlockDeviceMapping.1.Ebs.VolumeType": "gp3",
        "BlockDeviceMapping.1.Ebs.DeleteOnTermination": "true",
        "MetadataOptions.HttpTokens": "required",
        "MetadataOptions.HttpPutResponseHopLimit": "1",
        "TagSpecification.1.ResourceType": "instance",
        "TagSpecification.1.Tag.1.Key": "Name", "TagSpecification.1.Tag.1.Value": "gs-llm-a1"}


@pytest.mark.parametrize("code,stock", [
    ("InsufficientInstanceCapacity", True), ("MaxSpotInstanceCountExceeded", True),
    ("VcpuLimitExceeded", True), ("UnauthorizedOperation", False),
    ("InvalidAMIID.NotFound", False)])
def test_launch_refusal_carries_the_aws_code_and_whether_it_is_stock(code, stock):
    c = _client(_error(code, "nope"))
    with pytest.raises(Ec2Error, match=f"{code}: RunInstances in eu-north-1: nope") as e:
        c.launch(Offer("eu-north-1", "eu-north-1a", "g7e.2xlarge", "spot", 1.16),
                 "n", "ami-1", {}, "sg", "profile")
    assert (e.value.code, e.value.stock) == (code, stock)


def test_list_instances_reads_each_region_and_tells_spot_from_on_demand():
    def described(*rows):
        items = "".join(
            f"<item><instancesSet><item><instanceId>{i}</instanceId><instanceType>g7e.2xlarge"
            f"</instanceType><launchTime>2026-09-18T11:39:00.000Z</launchTime><placement>"
            f"<availabilityZone>{zone}</availabilityZone></placement>{lifecycle}<tagSet><item>"
            f"<key>Name</key><value>{name}</value></item></tagSet></item></instancesSet></item>"
            for i, zone, name, lifecycle in rows)
        return _resp(text=f"<DescribeInstancesResponse {NS}><reservationSet>{items}"
                          f"</reservationSet></DescribeInstancesResponse>")
    c = _client(
        described(("i-1", "eu-north-1a", "gs-llm-a", "<instanceLifecycle>spot</instanceLifecycle>")),
        described(("i-2", "us-west-2c", "gs-llm-b", "")))
    got = c.list_instances(["eu-north-1", "us-west-2"], "gs-llm-")
    assert [(i.id, i.region, i.zone, i.name, i.market, i.launched_at) for i in got] == [
        ("i-1", "eu-north-1", "eu-north-1a", "gs-llm-a", "spot", 1789731540.0),
        ("i-2", "us-west-2", "us-west-2c", "gs-llm-b", "on-demand", 1789731540.0)]
    _, sent = _sent(c)
    assert (sent["Filter.1.Name"], sent["Filter.1.Value.1"]) == ("tag:Name", "gs-llm-*")
    assert (sent["Filter.2.Value.1"], sent["Filter.2.Value.2"]) == ("pending", "running")


def _spot_requests(*ids):
    items = "".join(f"<item><spotInstanceRequestId>{i}</spotInstanceRequestId></item>" for i in ids)
    return _resp(text=f"<DescribeSpotInstanceRequestsResponse {NS}><spotInstanceRequestSet>{items}"
                      f"</spotInstanceRequestSet></DescribeSpotInstanceRequestsResponse>")


def test_terminate_cancels_the_instances_live_spot_request_before_ending_it():
    c = _client(_spot_requests("sir-1"), _resp(text=f"<CancelSpotInstanceRequestsResponse {NS}/>"),
                _resp(text=f"<TerminateInstancesResponse {NS}/>"))
    c.terminate("eu-north-1", "i-1")
    sent = [_sent(c, i)[1] for i in range(3)]
    assert [s["Action"] for s in sent] == [
        "DescribeSpotInstanceRequests", "CancelSpotInstanceRequests", "TerminateInstances"]
    assert (sent[0]["Filter.1.Name"], sent[0]["Filter.1.Value.1"]) == ("instance-id", "i-1")
    assert (sent[0]["Filter.2.Value.1"], sent[0]["Filter.2.Value.2"]) == ("open", "active")
    assert sent[1]["SpotInstanceRequestId.1"] == "sir-1"
    assert sent[2]["InstanceId.1"] == "i-1"


def test_terminate_with_no_live_request_cancels_nothing_and_a_failed_cancel_ends_nothing():
    c = _client(_spot_requests(), _resp(text=f"<TerminateInstancesResponse {NS}/>"))
    c.terminate("eu-north-1", "i-on-demand")
    assert [_sent(c, i)[1]["Action"] for i in range(2)] == [
        "DescribeSpotInstanceRequests", "TerminateInstances"]

    c = _client(_spot_requests("sir-1"), _error("RequestLimitExceeded"))
    with pytest.raises(Ec2Error, match="RequestLimitExceeded"):
        c.terminate("eu-north-1", "i-1")
    assert c.session.post.call_count == 2


def test_terminate_tolerates_a_gone_instance_and_raises_anything_else():
    c = _client(_spot_requests(), _error("InvalidInstanceID.NotFound"),
                _spot_requests(), _error("UnauthorizedOperation"))
    c.terminate("eu-north-1", "i-gone")
    with pytest.raises(Ec2Error, match="UnauthorizedOperation"):
        c.terminate("eu-north-1", "i-1")
