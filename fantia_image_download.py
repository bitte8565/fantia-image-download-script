# -*- coding: utf-8 -*-

import json
import os
import shutil
import sys
import time
import traceback
import urllib
import zipfile
from configparser import ConfigParser
from html.parser import HTMLParser
from logging import basicConfig, getLogger, StreamHandler, INFO
from pathlib import Path
from typing import Dict, List
from urllib.parse import urljoin

import requests

# 定数宣言
FANTIA_URL_PREFIX = 'https://fantia.jp/'
FANTIA_API_ENDPOINT = urljoin(FANTIA_URL_PREFIX, '/api/v1')
POSTED_AT_FORMAT = '%a, %d %b %Y %H:%M:%S %z'

# logger
script_file_path = Path(__file__)
basicConfig(filename=script_file_path.with_suffix('.log'), level=INFO,
            format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = getLogger(__name__)
logger.addHandler(StreamHandler(sys.stdout))

# config読み込み
ini_file_path = script_file_path.with_suffix('.ini')
config: ConfigParser = ConfigParser()
config.read(ini_file_path.absolute(), encoding='UTF-8')
fantia_config = config['fantia']

# config値
cookies = {'_session_id': fantia_config['session_id'].strip()}
fan_club_id: str = fantia_config['fan_club_id'].strip()
download_interval_seconds: int = int(fantia_config['download_interval_seconds'])
max_page: int = int(fantia_config['max_page'])
download_root_dir_path: Path = Path(fantia_config['download_root_dir'])
photo_flg = True if fantia_config['photo_flg'] == 'True' else False

# パス
download_root_dir: Path = download_root_dir_path if download_root_dir_path.is_absolute(
) else (script_file_path.parent / download_root_dir_path).absolute()
fan_club_id_dir: Path = download_root_dir / fan_club_id
zip_dir: Path = fan_club_id_dir / "zip"
temp_dir: Path = fan_club_id_dir / "temp"

# リクエスト
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_13_5) ' \
     'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/67.0.3396.99 Safari/537.36 '
HEADERS = {'User-Agent': UA}
download_list = []

# ダウンロード対象外拡張子
denny_extensions = ('.psd', '.txt')


class FantiaFanClubsParser(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self)
        self.posts_urls: List[str] = []
        self.max_page_number: int = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def handle_starttag(self, tag, attrs):
        try:
            self.page_link = False

            if tag == 'a':
                for attr in attrs:
                    if attr[0] != 'class':
                        continue

                    if attr[1] == 'link-block':
                        self.posts_urls.append(
                            get_attr_value_by_name(attrs, 'href'))
                        break
                    elif attr[1] == 'page-link':
                        page_link_url = get_attr_value_by_name(attrs, 'href')
                        if page_link_url:
                            self.max_page_number = int(page_link_url.split('=')[1])
                            break
        except Exception:
            # 2ページ目移行でエラーになるので暫定処置
            pass


class FantiaOriginalUriParser(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self)
        self.src = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def handle_starttag(self, tag, attrs):
        if tag == 'img':
            self.src = get_attr_value_by_name(attrs, 'src')


def get_attr_value_by_name(attrs: List[tuple], attr_name: str) -> str:
    attr = tuple(filter(lambda attr1: attr1[0] == attr_name, attrs))
    return attr[0][1] if attr else None


def download_interval() -> None:
    time.sleep(download_interval_seconds)


def get_uri(data):
    posts: Dict[any] = json.loads(data)['post']

    post_contents: List[dict] = posts['post_contents']
    for post_content in post_contents:

        if photo_flg:
            # 画像uri
            post_content_photos = post_content.get('post_content_photos')
            if post_content_photos:
                for post_content_photo in post_content_photos:
                    original_uri_response = requests.get(
                        urljoin(FANTIA_URL_PREFIX, post_content_photo['show_original_uri'])
                        , cookies=cookies, headers=HEADERS)
                    with FantiaOriginalUriParser() as original_uri_parser:
                        original_uri_parser.feed(original_uri_response.text)

                    content = {'uri': original_uri_parser.src, 'photo_flg': True}
                    download_list.append(content)
        else:
            # ファイルuri
            download_uri = post_content.get('download_uri')
            filename = post_content.get('filename')
            if download_uri and filename:
                path, ext = os.path.splitext(os.path.basename(filename))
                content = {'uri': download_uri, 'filename': filename, 'extension': ext, 'photo_flg': False}
                download_list.append(content)


def posts_parse(posts_url: str) -> None:
    # 直接開くと動的ページとなるが、APIでJSONを呼び出し可能
    posts_api_url = FANTIA_API_ENDPOINT + posts_url
    posts_response = requests.get(posts_api_url, cookies=cookies, headers=HEADERS)
    get_uri(posts_response.text)


def download_content(content):
    if photo_flg:
        parse_result = urllib.parse.urlparse(content['uri'])
        file_name = str(parse_result.path.split('/')[-1])
        download_file_path = str(fan_club_id_dir / file_name)

        # ダウンロード
        response = requests.get(content['uri'], cookies=cookies, headers=HEADERS)

    else:
        ext = content['extension']
        post_number = content['uri'].split('/')[2]
        file_number = content['uri'].split('/')[4]

        if ext.endswith(denny_extensions):
            return

        # ダウンロード
        response = requests.get(
            urljoin(FANTIA_URL_PREFIX, content['uri']), cookies=cookies, headers=HEADERS)

        if ext == '.zip':
            file_name = f'{post_number}_{file_number}{ext}'
            download_file_path = str(zip_dir / file_name)
        else:
            content_name = content['filename']
            file_name = f'{post_number}_{file_number}_{content_name}'
            download_file_path = str(fan_club_id_dir / file_name)

    # ファイルに書き込み
    with open(download_file_path, 'wb') as download_file:
        download_file.write(response.content)
        logger.info(f'download {download_file_path}')


def fan_clubs_page_parse(fan_clubs_url: str, page_number: int) -> None:
    fan_clubs_params = {'page': page_number}
    fan_clubs_response = requests.get(
        fan_clubs_url, params=fan_clubs_params, cookies=cookies, headers=HEADERS)

    with FantiaFanClubsParser() as fan_clubs_parser:
        fan_clubs_parser.feed(fan_clubs_response.text)
        posts_urls = fan_clubs_parser.posts_urls
        posts_count = len(posts_urls)

        for i, posts_url in enumerate(posts_urls, 1):
            log_message = f'post {i}/{posts_count}: [{posts_url}] parse '
            posts_parse(posts_url)
            logger.info(log_message + 'end.')


def fan_clubs_parse() -> None:
    # ファンクラブトップページを解析
    fan_clubs_url: str = urljoin(
        FANTIA_URL_PREFIX, f'/fanclubs/{fan_club_id}/posts')
    fan_clubs_response = requests.get(fan_clubs_url, cookies=cookies, headers=HEADERS)

    # 最終ページ取得
    with FantiaFanClubsParser() as fan_clubs_parser:
        fan_clubs_parser.feed(fan_clubs_response.text)
        max_page_number = fan_clubs_parser.max_page_number

    # ダウンロードするページ数を設定
    if max_page > 0:
        max_page_number = max_page

    # 各ページからダウンロードuriを取得
    for i in range(max_page_number):
        page_number = i + 1
        log_message = f'page {page_number}/{max_page_number} parse '
        logger.info(log_message + 'start.')
        fan_clubs_page_parse(fan_clubs_url, page_number)
        logger.info(log_message + 'end.')


def download():
    if not zip_dir.is_dir():
        zip_dir.mkdir(parents=True)
    if not temp_dir.is_dir():
        temp_dir.mkdir(parents=True)
    for content in download_list:
        download_interval()
        download_content(content)


def zip_open():
    zip_list = os.listdir(path=zip_dir)
    for zip_file in zip_list:
        zip_file_full_path = str(zip_dir / str(zip_file))
        open_dir = str(temp_dir / os.path.splitext(os.path.basename(str(zip_file)))[0])
        try:
            with zipfile.ZipFile(zip_file_full_path) as z:
                for info in z.infolist():
                    info.filename = info.orig_filename.encode('cp437').decode('cp932')
                    if os.sep != "/" and os.sep in info.filename:
                        info.filename = info.filename.replace(os.sep, "/")
                    if info.filename.endswith(denny_extensions):
                        continue
                    z.extract(info, open_dir)
        except zipfile.BadZipFile:
            pass


def move_file():
    for root, dirs, files in os.walk(top=str(temp_dir)):
        for file in files:
            file_path = os.path.join(root, file)
            tmp = file_path.split('\\')
            zip_file_name = ''
            for i in range(len(tmp)):
                if tmp[i] == 'temp':
                    zip_file_name = tmp[i + 1]
                    break
            shutil.move(file_path, fan_club_id_dir / f'{zip_file_name}_{file}')


def delete_dir():
    shutil.rmtree(str(zip_dir))
    shutil.rmtree(str(temp_dir))


def main():
    log_message = f'fan club [{fan_club_id}] parse '
    logger.info(log_message + 'start.')
    # サイト解析
    fan_clubs_parse()
    # ダウンロード
    download()
    # 解凍
    zip_open()
    # ファイル移動
    move_file()
    # 不要ファイル削除
    delete_dir()
    logger.info(log_message + 'end.')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        logger.error(e)
        logger.error(traceback.format_exc())
        sys.exit(1)
    finally:
        sys.exit(0)
