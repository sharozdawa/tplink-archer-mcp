"""Legacy TP-Link client for routers using the `/cgi_gdpr` GDPR-encrypted protocol.

The upstream repo targets the modern Vue UI (AX53) where every call goes through
the `update-store` module. Older Archer firmware (e.g. Archer C5 v4, firmware
3.16.x) instead speaks a multiline "action" protocol over `/cgi_gdpr`:

    POST /cgi_gdpr
    sign=<RSA signature of "key=<k>&iv=<iv>&h=<md5>&s=<seq>" >\r\n
    data=<AES-CBC(pkcs7) of "8\r\n[/cgi/login#0,0,0,0,0,0#0,0,0,0,0,0]0,5\r\nUserName=...\r\nPasswd=...\r\n">\r\n

`tplinkrouterc6u` already implements this protocol in `TPLinkMRClientBase`, but
its auto-detection uses `/cgi/getParm` and it sends form-encoded bodies. This
firmware serves the RSA key at `/cgi/getGDPRParm` and requires
`Content-Type: text/plain` + an `Origin` header, otherwise the server replies
with a headerless chunked body that `requests` cannot parse.

This module subclasses `TPLinkMRClientBase` with those three fixes and nothing
else, so all the inherited read/write helpers work as-is.
"""
from __future__ import annotations

import logging
from base64 import b64encode

import requests
from tplinkrouterc6u.client.mr import TPLinkMRClientBase

_log = logging.getLogger(__name__)

RSA_KEY_PATH = "cgi/getGDPRParm"
PROBE_TIMEOUT = 5


def is_legacy_router(host: str, timeout: float = PROBE_TIMEOUT) -> bool:
    """Return True when the router serves the legacy `/cgi/getGDPRParm` key page."""
    url = f"http://{host}/{RSA_KEY_PATH}"
    try:
        resp = requests.post(
            url,
            headers={"Referer": f"http://{host}/", "User-Agent": "Mozilla/5.0"},
            timeout=timeout,
            verify=False,
        )
    except requests.RequestException:
        return False
    text = resp.text or ""
    return "var nn=" in text and "var seq=" in text


class LegacyRouterClient(TPLinkMRClientBase):
    """TP-Link MR-style client patched for the `/cgi_gdpr` Archer C5 firmware."""

    ROUTER_NAME = "TP-Link Archer (legacy cgi_gdpr)"

    def __init__(self, host: str, password: str, username: str = "admin",
                 logger: logging.Logger | None = None, verify_ssl: bool = True,
                 timeout: int = 30) -> None:
        super().__init__(host, password, username, logger, verify_ssl, timeout)
        # The key page lives at /cgi/getGDPRParm on this firmware family.
        self._url_rsa_key = RSA_KEY_PATH
        # The CGI dispatcher needs text/plain + Origin, else it emits a
        # headerless chunked response that requests cannot parse.
        self.HEADERS = dict(self.HEADERS)
        self.HEADERS["Content-Type"] = "text/plain"
        self.HEADERS["Origin"] = self.host

    def _req_login(self) -> None:
        """POST the CGI login action to /cgi_gdpr."""
        acts = [
            self.ActItem(
                self.ActItem.CGI,
                "/cgi/login",
                attrs=[
                    "UserName=" + b64encode(self.username.encode()).decode(),
                    "Passwd=" + b64encode(self.password.encode()).decode(),
                ],
            )
        ]
        act_types, act_data = self._fill_acts(acts)
        data = "&".join(act_types) + "\r\n" + "".join(act_data)

        code, response = self._request(
            self._get_url("cgi_gdpr"), data_str=data, encrypt=True, is_login=True
        )
        if code != 200:
            raise RuntimeError(f"login request failed with HTTP {code}: {response[:200]}")

        ret = self._parse_ret_val(response)
        if ret == self.HTTP_ERR_USER_PWD_NOT_CORRECT:
            raise RuntimeError("login failed: wrong username or password")
        if ret != self.HTTP_RET_OK:
            raise RuntimeError(f"login failed with router return code {ret}")

    def request(self, path: str, body: str = "") -> dict:
        """The AX53-style `admin/...?form=...` API does not exist on this firmware.

        The direct tools that call `request()` (get_wifi_config, set_dns,
        get_wireguard_status, router_api_call, ...) target the modern Vue UI and
        cannot work here. Use the typed helpers instead (get_status,
        get_clients, get_ipv4_dhcp_leases, get_ipv4_reservations,
        get_ipv4_status, get_firmware, reboot, set_wifi).
        """
        raise NotImplementedError(
            f"Legacy Archer firmware has no '{path}' endpoint. The AX53-style "
            "admin/...?form=... API is unavailable; use the typed helpers "
            "(get_status, get_clients, get_ipv4_dhcp_leases, "
            "get_ipv4_reservations, get_ipv4_status, get_firmware, reboot, set_wifi) "
            "or call req_act() directly for raw CGI actions."
        )


def build_legacy_client(host: str, password: str, username: str = "admin",
                        logger: logging.Logger | None = None,
                        timeout: int = 20) -> LegacyRouterClient:
    """Construct and authorize a legacy client."""
    client = LegacyRouterClient(
        host=f"http://{host}",
        password=password,
        username=username,
        logger=logger or _log,
        verify_ssl=False,
        timeout=timeout,
    )
    client.authorize()
    return client
