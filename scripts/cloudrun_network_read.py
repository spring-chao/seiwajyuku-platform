"""Fixed control-plane reads used only by the authorized VPC repair mode.

These functions cannot create or modify a network, database, route or resource.
They do not connect to MySQL or retrieve business data.
"""

from tencentcloud.common.abstract_client import AbstractClient
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile
from tencentcloud.common.retry import NoopRetryer
from tencentcloud.tcb.v20180608 import models
from tencentcloud.tcb.v20180608.tcb_client import TcbClient

ENV_ID = "shengheshu-d2g2zyyl99f6c6fc2"
REGION = "ap-shanghai"


def _profile(endpoint):
    return ClientProfile(httpProfile=HttpProfile(endpoint=endpoint, reqTimeout=20), retryer=NoopRetryer())


def database_network(credential):
    client = TcbClient(credential, REGION, _profile("tcb.tencentcloudapi.com"))
    request = models.DescribeMySQLClusterDetailRequest()
    request.EnvId = ENV_ID
    import json

    return json.loads(client.DescribeMySQLClusterDetail(request).to_json_string())


class _VpcReadClient(AbstractClient):
    _apiVersion = "2017-03-12"
    _endpoint = "vpc.tencentcloudapi.com"
    _service = "vpc"


def vpc_subnet(credential, vpc_id, subnet_id):
    import re

    if not re.fullmatch(r"vpc-[a-z0-9]+", vpc_id) or not re.fullmatch(r"subnet-[a-z0-9]+", subnet_id):
        raise ValueError("invalid network identity")
    client = _VpcReadClient(credential, REGION, _profile("vpc.tencentcloudapi.com"))
    vpcs = client.call_json("DescribeVpcs", {"VpcIds": [vpc_id]})["Response"]
    subnets = client.call_json("DescribeSubnets", {"SubnetIds": [subnet_id]})["Response"]
    return {"VpcSet": vpcs.get("VpcSet", []), "SubnetSet": subnets.get("SubnetSet", [])}
