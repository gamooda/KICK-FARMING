# Based on https://github.com/derrod/twl.py
# Original Copyright (c) 2020 Rodney
# The MIT License (MIT)

import copy
# import getpass
import logging
import os

# import webbrowser
# import browser_cookie3

import requests

from TwitchChannelPointsMiner.classes.Exceptions import (
    BadCredentialsException,
    WrongCookiesException,
)
from TwitchChannelPointsMiner.constants import CLIENT_ID, GQLOperations, USER_AGENTS
from TwitchChannelPointsMiner.storage import load_secure_json, save_secure_json

from datetime import datetime, timedelta, timezone
from time import sleep

logger = logging.getLogger(__name__)

"""def interceptor(request) -> str:
    if (
        request.method == 'POST'
        and request.url == 'https://passport.twitch.tv/protected_login'
    ):
        import json
        body = request.body.decode('utf-8')
        data = json.loads(body)
        data['client_id'] = CLIENT_ID
        request.body = json.dumps(data).encode('utf-8')
        del request.headers['Content-Length']
        request.headers['Content-Length'] = str(len(request.body))"""


class TwitchLogin(object):
    __slots__ = [
        "client_id",
        "device_id",
        "token",
        "refresh_token",
        "expires_in",
        "login_check_result",
        "session",
        "username",
        "password",
        "user_id",
        "scopes",
        "email",
        "cookies",
        "shared_cookies",
        "cancelled",
        "last_error",
        "pending_checks"
    ]

    def __init__(self, client_id, device_id, username, user_agent, password=None):
        self.client_id = client_id
        self.device_id = device_id
        self.token = None
        self.refresh_token = None
        self.expires_in = 0
        self.login_check_result = False
        self.session = requests.session()
        self.session.headers.update(
            {"Client-ID": self.client_id,
                "X-Device-Id": self.device_id, "User-Agent": user_agent}
        )
        self.username = username
        self.password = password
        self.user_id = None
        self.scopes = ()
        self.email = None

        self.cookies = []
        self.shared_cookies = []
        self.cancelled = False
        self.last_error = ""
        self.pending_checks = 0

    def login_flow(self):
        self.last_error = ""
        self.pending_checks = 0
        logger.info("You'll have to login to Twitch!")
        scopes = "channel_read chat:read user_blocks_edit user_blocks_read user_follows_edit user_read user:read:follows"
        post_data = {
            "client_id": self.client_id,
            "scopes": scopes,
        }
        while not self.cancelled:
            logger.info("Trying the TV login method..")
            try:
                response = self.send_oauth_request("https://id.twitch.tv/oauth2/device", post_data)
            except requests.RequestException as error:
                self.last_error = "Не удалось связаться с Twitch: " + str(error)[:300]
                logger.error("Twitch device authorization request failed: %s", error)
                return False
            if response.status_code != 200:
                try:
                    detail = response.json().get("message", response.text[:200])
                except ValueError:
                    detail = response.text[:200]
                self.last_error = "Twitch отклонил запрос кода: HTTP %s · %s" % (response.status_code, detail)
                logger.error(self.last_error)
                return False
            try:
                payload = response.json()
            except ValueError:
                self.last_error = "Twitch вернул некорректный ответ при создании кода."
                logger.error(self.last_error)
                return False
            if not payload.get("user_code") or not payload.get("device_code"):
                self.last_error = "Twitch не вернул код подтверждения."
                logger.error(self.last_error)
                return False
            user_code = payload["user_code"]
            device_code = payload["device_code"]
            interval = max(1, int(payload.get("interval", 5)))
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(payload.get("expires_in", 1800)))
            verification_uri = payload.get("verification_uri", "https://www.twitch.tv/activate")
            logger.info("DEVICE_AUTH_URL: %s", verification_uri)
            logger.info("and enter this code: %s", user_code)
            logger.info("Hurry up! It will expire in %s minutes!", int(payload.get("expires_in", 1800) / 60))
            logger.info("DEVICE_AUTH_PENDING: Ожидаю подтверждения этого кода в Twitch.")
            token_data = {
                "client_id": self.client_id,
                "scopes": scopes,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            }
            self.pending_checks = 0
            while not self.cancelled:
                sleep(interval)
                if self.cancelled:
                    return False
                if datetime.now(timezone.utc) >= expires_at:
                    self.last_error = "Срок действия кода Twitch истёк. Запрашиваю новый код."
                    logger.error(self.last_error)
                    break
                try:
                    token_response = self.send_oauth_request("https://id.twitch.tv/oauth2/token", token_data)
                except requests.RequestException as error:
                    self.pending_checks += 1
                    self.last_error = "Ошибка связи при проверке кода: " + str(error)[:250]
                    logger.warning("DEVICE_AUTH_NETWORK_RETRY: %s", self.last_error)
                    continue
                if token_response.status_code != 200:
                    try:
                        error_payload = token_response.json()
                        error_message = str(error_payload.get("message", ""))
                    except ValueError:
                        error_message = token_response.text[:200]
                    if error_message == "authorization_pending":
                        self.pending_checks += 1
                        if self.pending_checks == 1 or self.pending_checks % 6 == 0:
                            logger.info("DEVICE_AUTH_PENDING: Twitch пока не вернул подтверждение. Проверка продолжается.")
                        continue
                    if error_message == "slow_down":
                        interval += 5
                        logger.warning("DEVICE_AUTH_SLOW_DOWN: увеличен интервал проверки до %s сек.", interval)
                        continue
                    if error_message in ("expired_token", "invalid device code"):
                        self.last_error = "Код подтверждения уже истёк или был использован. Запрашиваю новый код."
                        logger.error(self.last_error)
                        break
                    if error_message == "access_denied":
                        self.last_error = "Вход был отклонён в Twitch."
                        logger.error(self.last_error)
                        return False
                    self.last_error = "Ошибка проверки кода Twitch: HTTP %s · %s" % (token_response.status_code, error_message or "неизвестная ошибка")
                    logger.error(self.last_error)
                    return False
                try:
                    token_payload = token_response.json()
                except ValueError:
                    self.last_error = "Twitch вернул некорректный ответ с токеном."
                    logger.error(self.last_error)
                    return False
                if token_payload.get("access_token"):
                    self.refresh_token = token_payload.get("refresh_token")
                    self.set_token(token_payload["access_token"])
                    logger.info("DEVICE_AUTH_APPROVED: Twitch подтвердил код. Проверяю аккаунт.")
                    if self.check_login():
                        logger.info("DEVICE_AUTH_VALIDATED: Аккаунт Twitch подтверждён.")
                        self.last_error = ""
                        return True
                    self.last_error = self.last_error or "Twitch выдал токен, но проверка аккаунта не прошла."
                    logger.error("Twitch issued a token, but account verification failed: %s", self.last_error)
                    return False
                self.last_error = "Twitch ответил без токена доступа."
                logger.error(self.last_error)
                return False
            if self.cancelled:
                return False
        return False

    def cancel(self):
        self.cancelled = True

    def set_token(self, new_token):
        self.token = new_token
        self.login_check_result = False
        self.session.headers.update({"Authorization": f"Bearer {self.token}"})

    # def send_login_request(self, json_data):
    def refresh_access_token(self):
        if not self.refresh_token:
            return False
        try:
            response = self.send_oauth_request("https://id.twitch.tv/oauth2/token", {
                "client_id": self.client_id,
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            })
        except requests.RequestException as error:
            logger.warning(f"Twitch session refresh failed: {error}")
            return False
        if response.status_code != 200:
            try:
                detail = response.json().get("message", response.text[:200])
            except ValueError:
                detail = response.text[:200]
            if "missing client secret" in str(detail).lower():
                logger.info("Twitch client requires device reauthorization; a new approval will be requested.")
            else:
                logger.warning(f"Twitch session refresh rejected: HTTP {response.status_code} · {detail}")
            return False
        try:
            payload = response.json()
        except ValueError:
            return False
        if not payload.get("access_token"):
            return False
        self.refresh_token = payload.get("refresh_token", self.refresh_token)
        self.set_token(payload["access_token"])
        return self.check_login()

    def send_oauth_request(self, url, json_data):
        # response = self.session.post("https://passport.twitch.tv/protected_login", json=json_data)
        """response = self.session.post("https://passport.twitch.tv/login", json=json_data, headers={
            'Accept': 'application/vnd.twitchtv.v3+json',
            'Accept-Encoding': 'gzip',
            'Accept-Language': 'en-US',
            'Content-Type': 'application/json; charset=UTF-8',
            'Host': 'passport.twitch.tv'
        },)"""
        response = self.session.post(url, data=json_data, headers={
            'Accept': 'application/json',
            'Accept-Encoding': 'gzip',
            'Accept-Language': 'en-US',
            "Cache-Control": "no-cache",
            "Client-Id": self.client_id,
            "Host": "id.twitch.tv",
            "Origin": "https://android.tv.twitch.tv",
            "Pragma": "no-cache",
            "Referer": "https://android.tv.twitch.tv/",
            "User-Agent": USER_AGENTS["Android"]["TV"],
            "X-Device-Id": self.device_id
        }, timeout=20)
        return response

    def login_flow_backup(self, password=None):
        """Backup OAuth Selenium login
        from undetected_chromedriver import ChromeOptions
        import seleniumwire.undetected_chromedriver.v2 as uc
        from selenium.webdriver.common.by import By
        from time import sleep

        HEADLESS = False

        options = uc.ChromeOptions()
        if HEADLESS is True:
            options.add_argument('--headless')
        options.add_argument('--log-level=3')
        options.add_argument('--disable-web-security')
        options.add_argument('--allow-running-insecure-content')
        options.add_argument('--lang=en')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-gpu')
        # options.add_argument("--user-agent=\"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36\"")
        # options.add_argument("--window-size=1920,1080")
        # options.set_capability("detach", True)

        logger.info(
            'Now a browser window will open, it will login with your data.')
        driver = uc.Chrome(
            options=options, use_subprocess=True  # , executable_path=EXECUTABLE_PATH
        )
        driver.request_interceptor = interceptor
        driver.get('https://www.twitch.tv/login')

        driver.find_element(By.ID, 'login-username').send_keys(self.username)
        driver.find_element(By.ID, 'password-input').send_keys(password)
        sleep(0.3)
        driver.execute_script(
            'document.querySelector("#root > div > div.scrollable-area > div.simplebar-scroll-content > div > div > div > div.Layout-sc-nxg1ff-0.gZaqky > form > div > div:nth-child(3) > button > div > div").click()'
        )

        logger.info(
            'Enter your verification code in the browser and wait for the Twitch website to load, then press Enter here.'
        )
        input()

        logger.info("Extracting cookies...")
        self.cookies = driver.get_cookies()
        # print(self.cookies)
        # driver.close()
        driver.quit()
        self.username = self.get_cookie_value("login")
        # print(f"self.username: {self.username}")

        if not self.username:
            logger.error("Couldn't extract login, probably bad cookies.")
            return False

        return self.get_cookie_value("auth-token")"""

        # logger.error("Backup login flow is not available. Use a VPN or wait a while to avoid the CAPTCHA.")
        # return False

        """Backup OAuth login flow in case manual captcha solving is required"""
        browser = input(
            "What browser do you use? Chrome (1), Firefox (2), Other (3): "
        ).strip()
        if browser not in ("1", "2"):
            logger.info("Your browser is unsupported, sorry.")
            return None

        input(
            "Please login inside your browser of choice (NOT incognito mode) and press Enter..."
        )
        logger.info("Loading cookies saved on your computer...")
        twitch_domain = ".twitch.tv"
        if browser == "1":  # chrome
            cookie_jar = browser_cookie3.chrome(domain_name=twitch_domain)
        else:
            cookie_jar = browser_cookie3.firefox(domain_name=twitch_domain)
        # logger.info(f"cookie_jar: {cookie_jar}")
        cookies_dict = requests.utils.dict_from_cookiejar(cookie_jar)
        # logger.info(f"cookies_dict: {cookies_dict}")
        self.username = cookies_dict.get("login")
        self.shared_cookies = cookies_dict
        return cookies_dict.get("auth-token")

    def validate_token(self):
        if not self.token:
            self.last_error = "Отсутствует токен Twitch."
            return False
        try:
            response = self.session.get(
                "https://id.twitch.tv/oauth2/validate",
                headers={"Authorization": f"OAuth {self.token}"},
                timeout=15,
            )
        except requests.RequestException as error:
            self.last_error = "Не удалось проверить токен Twitch: " + str(error)[:300]
            logger.warning(self.last_error)
            return False
        if response.status_code != 200:
            self.last_error = "Twitch не подтвердил токен: HTTP %s." % response.status_code
            logger.warning(self.last_error)
            return False
        try:
            payload = response.json()
        except ValueError:
            self.last_error = "Twitch вернул некорректный ответ проверки токена."
            logger.warning(self.last_error)
            return False
        if payload.get("client_id") != self.client_id:
            self.last_error = "Выданный токен относится к другому Twitch Client ID."
            logger.error(self.last_error)
            return False
        self.scopes = tuple(str(scope) for scope in payload.get("scopes", []) if scope)
        actual_login = str(payload.get("login") or "").strip().lower()
        if actual_login:
            if self.username and actual_login != self.username.lower():
                logger.info("DEVICE_AUTH_ACCOUNT_SELECTED: вместо введённого логина авторизован аккаунт @%s.", actual_login)
            self.username = actual_login
        user_id = payload.get("user_id")
        if not user_id:
            self.last_error = "В ответе Twitch отсутствует ID авторизованного аккаунта."
            logger.warning(self.last_error)
            return False
        self.user_id = str(user_id)
        self.expires_in = int(payload.get("expires_in") or 0)
        self.login_check_result = True
        self.last_error = ""
        return True

    def check_login(self):
        return self.validate_token()

    def save_cookies(self, cookies_file):
        cookies_dict = self.session.cookies.get_dict()
        cookies_dict["auth-token"] = self.token
        if self.refresh_token:
            cookies_dict["refresh-token"] = self.refresh_token
        if "persistent" not in cookies_dict:
            cookies_dict["persistent"] = self.user_id
        self.cookies = [
            {"name": cookie_name, "value": value}
            for cookie_name, value in cookies_dict.items()
        ]
        save_secure_json(cookies_file, self.cookies)

    def get_cookie_value(self, key):
        for cookie in self.cookies:
            if cookie["name"] == key:
                if cookie["value"] is not None:
                    return cookie["value"]
        return None

    def load_cookies(self, cookies_file):
        if os.path.isfile(cookies_file):
            self.cookies = load_secure_json(cookies_file)
            if not isinstance(self.cookies, list):
                raise WrongCookiesException("The saved Twitch session is invalid.")
            self.refresh_token = self.get_cookie_value("refresh-token")
        else:
            raise WrongCookiesException("There must be a cookies file!")

    def get_user_id(self):
        persistent = self.get_cookie_value("persistent")
        if persistent is not None:
            try:
                return int(str(persistent).split("%")[0])
            except (TypeError, ValueError):
                pass
        return int(self.user_id) if self.user_id is not None else None

    def __set_user_id(self):
        json_data = copy.deepcopy(GQLOperations.GetIDFromLogin)
        json_data["variables"]["login"] = self.username
        response = self.session.post(GQLOperations.url, json=json_data, timeout=20)

        if response.status_code == 200:
            json_response = response.json()
            if (
                "data" in json_response
                and "user" in json_response["data"]
                and json_response["data"]["user"]["id"] is not None
            ):
                self.user_id = json_response["data"]["user"]["id"]
                return True
        return False

    def get_auth_token(self):
        return self.get_cookie_value("auth-token")
