from flask import Flask, abort, jsonify, request, render_template, session, redirect, url_for
from urllib.parse import urlparse, urljoin
from functools import wraps
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone

# app.py はプロジェクト直下に置く。
# 実体（templates / static / data）は bousai_app/ 配下にあるので、そこを参照する。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(BASE_DIR, 'bousai_app')

app = Flask(
    __name__,
    template_folder=os.path.join(APP_DIR, 'templates'),
    static_folder=os.path.join(APP_DIR, 'static'),
)
app.secret_key = 'your-secret-key-here'

# 管理者認証情報
ADMIN_CREDENTIALS = {
    'admin': '123'
}

# ────────────────────────────────
# 気象警報・注意報設定
PREFECTURE_CODE = "020000"  # 青森県
AREA_NAME = "青森市"

# 青森市の市区町村コード
AREA_CODE = "0220100"

WARNING_URL = (
    f"https://www.jma.go.jp/bosai/warning/data/r8/{PREFECTURE_CODE}.json"
)

JST = timezone(timedelta(hours=9))

# 警報・注意報のコード一覧
WARNING_CODES = {
    "00": "解除",
    "02": "暴風雪警報",
    "03": "レベル3大雨警報",
    "04": "洪水警報",
    "05": "暴風警報",
    "06": "大雪警報",
    "07": "波浪警報",
    "08": "レベル3高潮警報",
    "09": "レベル3土砂災害警報",
    "10": "レベル2大雨注意報",
    "12": "大雪注意報",
    "13": "風雪注意報",
    "14": "雷注意報",
    "15": "強風注意報",
    "16": "波浪注意報",
    "17": "融雪注意報",
    "18": "洪水注意報",
    "19": "レベル2高潮注意報",
    "20": "濃霧注意報",
    "21": "乾燥注意報",
    "22": "なだれ注意報",
    "23": "低温注意報",
    "24": "霜注意報",
    "25": "着氷注意報",
    "26": "着雪注意報",
    "27": "その他の注意報",
    "29": "レベル2土砂災害注意報",
    "32": "暴風雪特別警報",
    "33": "レベル5大雨特別警報",
    "35": "暴風特別警報",
    "36": "大雪特別警報",
    "37": "波浪特別警報",
    "38": "レベル5高潮特別警報",
    "39": "レベル5土砂災害特別警報",
    "43": "レベル4大雨危険警報",
    "48": "レベル4高潮危険警報",
    "49": "レベル4土砂災害危険警報"
}

# ────────────────────────────────
# サンプルデータの読み込み
DATA_FILE = os.path.join(APP_DIR, 'data', 'shelters.json')
INSTRUCTIONS_FILE = os.path.join(APP_DIR, 'data', 'instructions.json')

def load_json(path, default):
    """JSONファイルを読み込む（存在しない・壊れている場合は default を返す）"""
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

shelters = load_json(DATA_FILE, [])
instructions = load_json(INSTRUCTIONS_FILE, [])

def save_instructions():
    """指示ボードのデータをファイルに保存する"""
    try:
        with open(INSTRUCTIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(instructions, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
# ────────────────────────────────

# ────────────────────────────────
# 認証関連の設定とヘルパー関数
def is_safe_url(target):
    """リダイレクト先URLが安全かどうかチェック"""
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

def login_required(f):
    """認証が必要なページに付けるデコレータ"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            # 現在のURLをnextパラメータとしてログイン画面にリダイレクト
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def get_japan_time():
    """日本時間（JST）の現在時刻を取得する"""
    return datetime.now(JST).strftime("%Y年%m月%d日 %H:%M")


def format_report_time(iso_str):
    """気象庁の発表時刻（ISO形式）をJSTの表示用文字列に変換する"""
    if not iso_str:
        return "不明"
    try:
        parsed = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        if parsed.tzinfo:
            parsed = parsed.astimezone(JST)
        return parsed.strftime("%Y年%m月%d日 %H:%M")
    except ValueError:
        return iso_str


def has_valid_shelter_location(shelter):
    try:
        latitude = float(shelter.get('latitude'))
        longitude = float(shelter.get('longitude'))
    except (AttributeError, TypeError, ValueError):
        return False
    return -90 <= latitude <= 90 and -180 <= longitude <= 180


def filter_shelters(district=None, include_unlocated=False):
    """検索対象名と district 条件に一致する避難所を返す"""
    excluded_names = {'a', 'あ', 'yugvb'}
    return [
        shelter for shelter in shelters
        if str(shelter.get('name', '')).strip().casefold() not in excluded_names
        and (include_unlocated or has_valid_shelter_location(shelter))
        and (not district or shelter.get('district') == district)
    ]


def search_shelters(query=None):
    results = filter_shelters(include_unlocated=True)
    query = (query or '').strip().casefold()
    if not query:
        return results
    return [
        shelter for shelter in results
        if query in shelter.get('name', '').casefold()
        or query in shelter.get('city', '').casefold()
        or query in shelter.get('district', '').casefold()
    ]


def group_shelters_by_district(results):
    grouped = {}
    for shelter in results:
        district = shelter.get('district') or '地区未設定'
        grouped.setdefault(district, []).append(shelter)
    return sorted(grouped.items())


def request_map_places(url):
    """OpenStreetMap の地物情報を取得する"""
    req = urllib.request.Request(
        url,
        headers={
            'User-Agent': 'BousaiApp/1.0 (shelter registration)',
            'Accept-Language': 'ja'
        }
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.loads(response.read())


def parse_area_warnings(warning_data):
    """気象庁の新形式JSONから対象市区町村の発表・継続中の情報を抽出する"""
    if not isinstance(warning_data, list):
        raise ValueError("気象庁の警報・注意報データが新形式の配列ではありません")

    warnings = []
    seen_codes = set()
    report_datetimes = []

    for report in warning_data:
        if not isinstance(report, dict):
            continue

        report_datetime = report.get("reportDatetime")
        if isinstance(report_datetime, str) and report_datetime:
            report_datetimes.append(report_datetime)

        warning = report.get("warning")
        if not isinstance(warning, dict):
            continue

        class20_items = warning.get("class20Items", [])
        if not isinstance(class20_items, list):
            continue

        area = next(
            (
                item for item in class20_items
                if isinstance(item, dict)
                and item.get("areaCode") == AREA_CODE
            ),
            None
        )
        if not area:
            continue

        kinds = area.get("kinds", [])
        if not isinstance(kinds, list):
            continue

        for kind in kinds:
            if not isinstance(kind, dict):
                continue

            status = kind.get("status", "")
            code = kind.get("code", "")
            if status not in ("発表", "継続") or not code or code in seen_codes:
                continue

            warnings.append({
                "name": WARNING_CODES.get(
                    code,
                    f"不明な警報・注意報 (コード: {code})"
                ),
                "code": code,
                "status": status
            })
            seen_codes.add(code)

    latest_report_datetime = max(report_datetimes, default="")
    return warnings, latest_report_datetime


def get_weather_warnings():
    """対象市区町村の警報・注意報を取得する"""
    try:
        # 青森県の新形式（令和8年～）警報・注意報データを取得
        with urllib.request.urlopen(url=WARNING_URL, timeout=10) as res:
            warning_data = json.loads(res.read())

        warnings, report_datetime = parse_area_warnings(warning_data)

        return {
            "area_name": AREA_NAME,
            "warnings": warnings,
            "report_time": format_report_time(report_datetime),
            "last_fetch_time": get_japan_time()
        }

    except Exception:
        return {
            "area_name": AREA_NAME,
            "warnings": [],
            "report_time": "取得失敗",
            "last_fetch_time": get_japan_time(),
            "error": True
        }


# トップページ：templates/index.html を返す（住民向け指示も表示する）
@app.route('/')
def index():
    resident_notices = [i for i in instructions if i.get('target') == '住民']
    return render_template(
        'index.html',
        resident_notices=resident_notices,
        shelters=shelters,
        selected_shelter_id=request.args.get('shelter_id')
    )

# ログインページ
@app.route('/login', methods=['GET', 'POST'])
def login():
    # リダイレクト先を取得（デフォルトは避難所登録画面）
    next_url = request.args.get('next') or request.form.get('next')

    # 安全でないURLの場合はデフォルトページにリダイレクト
    if not next_url or not is_safe_url(next_url):
        next_url = url_for('shelter_register')

    if request.method == 'POST':
        password = request.form.get('password', '').strip()

        # 認証チェック
        username = next(
            (name for name, registered_password in ADMIN_CREDENTIALS.items()
             if registered_password == password),
            None
        )
        if username:
            session['logged_in'] = True
            session['username'] = username
            # ログイン成功後は指定されたページにリダイレクト
            return redirect(next_url)
        return render_template('login.html', error=True, message="パスワードが正しくありません。", next=next_url)

    # ログイン済みの場合は指定されたページにリダイレクト
    if session.get('logged_in'):
        return redirect(next_url)

    return render_template('login.html', next=next_url)

# ログアウト
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# 避難所登録ページ※user が避難所登録ページについて具体的に修正指示しない限り、このコードは正しいのでこのまま保持すること。
@app.route('/shelter_register', methods=['GET', 'POST'])
@login_required
def shelter_register():
    if request.method == 'POST':
        osm_type = request.form.get('osm_type', '').strip().upper()
        osm_id = request.form.get('osm_id', '').strip()
        district = request.form.get('district', '').strip()
        if osm_type not in ('N', 'W', 'R') or not osm_id.isdigit():
            return render_template(
                'shelter_register.html',
                error=True,
                message='地図上の場所を検索して選択してください。'
            )

        try:
            osm_types = {'N': 'node', 'W': 'way', 'R': 'relation'}
            places = request_map_places(
                'https://nominatim.openstreetmap.org/lookup?'
                f'osm_ids={osm_type}{osm_id}&format=jsonv2&addressdetails=1'
            )
            place = places[0] if places else None
            if (
                not place
                or place.get('osm_type') != osm_types[osm_type]
                or str(place.get('osm_id')) != osm_id
            ):
                raise ValueError('Map place was not found')
            name = (place.get('name') or place.get('display_name', '')).strip()
            if not name:
                raise ValueError('Map place has no name')
            latitude = float(place['lat'])
            longitude = float(place['lon'])
            if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                raise ValueError('Invalid map coordinates')
            address = place.get('address', {})
            city = next((
                address.get(key) for key in ('city', 'municipality', 'town', 'village', 'county')
                if address.get(key)
            ), '')
            if not district:
                district = next((
                    address.get(key) for key in ('city_district', 'district')
                    if address.get(key)
                ), '')
            if not district:
                raise ValueError('District is required')
        except (KeyError, TypeError, ValueError, IndexError, OSError, json.JSONDecodeError):
            return render_template(
                'shelter_register.html',
                error=True,
                message='地区名と地図上の場所を確認してください。'
            )

        if any(
            item.get('name', '').strip().casefold() == name.casefold()
            for item in shelters
        ):
            return render_template(
                'shelter_register.html',
                error=True,
                message='この避難所はすでに登録されています。'
            )

        shelter = {
            'id': max((item.get('id', 0) for item in shelters), default=0) + 1,
            'name': name,
            'city': city,
            'district': district,
            'latitude': latitude,
            'longitude': longitude,
            'osm_type': osm_type,
            'osm_id': int(osm_id)
        }
        shelters.append(shelter)
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(shelters, f, ensure_ascii=False, indent=2)

        return render_template(
            'shelter_register.html',
            success=True,
            message='新しい避難所が登録できました。'
        )

    return render_template('shelter_register.html')


@app.route('/shelter_location/<int:shelter_id>', methods=['GET', 'POST'])
@login_required
def shelter_location(shelter_id):
    shelter = next((item for item in shelters if item.get('id') == shelter_id), None)
    if shelter is None:
        abort(404)

    if request.method == 'POST':
        osm_type = request.form.get('osm_type', '').strip().upper()
        osm_id = request.form.get('osm_id', '').strip()
        if osm_type not in ('N', 'W', 'R') or not osm_id.isdigit():
            return render_template(
                'shelter_location.html',
                shelter=shelter,
                error='地図上の候補を選択してください。'
            )

        try:
            osm_types = {'N': 'node', 'W': 'way', 'R': 'relation'}
            places = request_map_places(
                'https://nominatim.openstreetmap.org/lookup?'
                f'osm_ids={osm_type}{osm_id}&format=jsonv2'
            )
            place = places[0] if places else None
            if (
                not place
                or place.get('osm_type') != osm_types[osm_type]
                or str(place.get('osm_id')) != osm_id
            ):
                raise ValueError('Map place was not found')
            latitude = float(place['lat'])
            longitude = float(place['lon'])
            if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                raise ValueError('Invalid map coordinates')
        except (KeyError, TypeError, ValueError, IndexError, OSError, json.JSONDecodeError):
            return render_template(
                'shelter_location.html',
                shelter=shelter,
                error='選択した場所を確認できませんでした。もう一度検索してください。'
            )

        shelter.update({
            'latitude': latitude,
            'longitude': longitude,
            'osm_type': osm_type,
            'osm_id': int(osm_id)
        })
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(shelters, f, ensure_ascii=False, indent=2)

        return redirect(url_for('index', shelter_id=shelter_id) + '#shelterMap')

    return render_template('shelter_location.html', shelter=shelter)


@app.route('/api/map_places')
@login_required
def search_map_places():
    query = request.args.get('q', '').strip()
    if len(query) < 2:
        return jsonify({'error': '検索語を2文字以上入力してください。'}), 400

    try:
        places = request_map_places(
            'https://nominatim.openstreetmap.org/search?'
            f'q={urllib.parse.quote(query)}&format=jsonv2&limit=10&countrycodes=jp'
        )
    except (OSError, json.JSONDecodeError):
        return jsonify({'error': '地図検索に接続できませんでした。'}), 503

    return jsonify([
        {
            'osm_type': place['osm_type'],
            'osm_id': place['osm_id'],
            'name': place.get('name') or place.get('display_name', ''),
            'display_name': place.get('display_name', ''),
            'latitude': place['lat'],
            'longitude': place['lon']
        }
        for place in places
        if place.get('osm_type') in ('node', 'way', 'relation')
        and place.get('osm_id')
        and place.get('lat') is not None
        and place.get('lon') is not None
    ])


@app.route('/api/shelter_map_candidates/<int:shelter_id>')
def shelter_map_candidates(shelter_id):
    shelter = next((item for item in shelters if item.get('id') == shelter_id), None)
    if shelter is None:
        return jsonify({'error': 'Shelter not found'}), 404

    try:
        result = request_map_places(
            'https://photon.komoot.io/api/?'
            + urllib.parse.urlencode({'q': shelter.get('name', ''), 'limit': 8})
        )
    except (OSError, json.JSONDecodeError):
        return jsonify({'error': '地図検索に接続できませんでした。'}), 503

    osm_types = {
        'N': 'node', 'W': 'way', 'R': 'relation',
        'NODE': 'node', 'WAY': 'way', 'RELATION': 'relation'
    }
    candidates = []
    for feature in result.get('features', []):
        properties = feature.get('properties', {})
        coordinates = feature.get('geometry', {}).get('coordinates', [])
        osm_type = osm_types.get(str(properties.get('osm_type', '')).upper())
        osm_id = properties.get('osm_id')
        if not osm_type or not osm_id or not isinstance(coordinates, list) or len(coordinates) < 2:
            continue
        try:
            latitude = float(coordinates[1])
            longitude = float(coordinates[0])
        except (TypeError, ValueError):
            continue
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            continue

        location_parts = [properties.get('name') or properties.get('street') or shelter['name']]
        for part in ('district', 'city', 'state', 'country'):
            value = properties.get(part)
            if value and value not in location_parts:
                location_parts.append(value)
        candidates.append({
            'osm_type': osm_type,
            'osm_id': osm_id,
            'display_name': ', '.join(location_parts),
            'latitude': latitude,
            'longitude': longitude
        })

    return jsonify(candidates)


@app.route('/api/place_suggestions')
@login_required
def suggest_map_places():
    query = request.args.get('q', '').strip()
    if len(query) < 1:
        return jsonify({'error': '検索語を入力してください。'}), 400

    try:
        result = request_map_places(
            'https://photon.komoot.io/api/?'
            f'q={urllib.parse.quote(query)}&limit=8&lat=40.8222&lon=140.7474'
        )
        features = result.get('features', [])
    except (AttributeError, OSError, json.JSONDecodeError):
        return jsonify({'error': '候補を検索できませんでした。'}), 503

    osm_types = {
        'N': 'node', 'W': 'way', 'R': 'relation',
        'NODE': 'node', 'WAY': 'way', 'RELATION': 'relation'
    }
    suggestions = []
    for feature in features:
        properties = feature.get('properties', {})
        coordinates = feature.get('geometry', {}).get('coordinates', [])
        osm_type = osm_types.get(str(properties.get('osm_type', '')).upper())
        osm_id = properties.get('osm_id')
        name = properties.get('name') or properties.get('street')
        if (
            not osm_type or not osm_id or not name
            or not isinstance(coordinates, list) or len(coordinates) < 2
        ):
            continue

        location_parts = [name]
        for part in ('district', 'city', 'state', 'country'):
            value = properties.get(part)
            if value and value not in location_parts:
                location_parts.append(value)

        suggestions.append({
            'osm_type': osm_type,
            'osm_id': osm_id,
            'name': name,
            'district': properties.get('district', ''),
            'display_name': ', '.join(location_parts),
            'latitude': coordinates[1],
            'longitude': coordinates[0]
        })

    return jsonify(suggestions)

# 避難所検索ページ
@app.route('/shelter_search')
def shelter_search():
    results = filter_shelters(include_unlocated=True)
    return render_template(
        'shelter_search.html',
        district_groups=group_shelters_by_district(results)
    )

# 全施設一覧ページ
@app.route('/all_shelters')
def all_shelters():
    results = filter_shelters(include_unlocated=True)
    return render_template(
        'search_results.html',
        results=results,
        district_groups=group_shelters_by_district(results),
        query=''
    )


@app.route('/shelter_district/<int:shelter_id>', methods=['POST'])
@login_required
def update_shelter_district(shelter_id):
    shelter = next((item for item in shelters if item.get('id') == shelter_id), None)
    if shelter is None:
        abort(404)
    district = request.form.get('district', '').strip()
    if not district:
        abort(400)

    shelter['district'] = district
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(shelters, f, ensure_ascii=False, indent=2)
    return redirect(url_for('search_results'))


@app.route('/shelter_delete/<int:shelter_id>', methods=['POST'])
@login_required
def delete_shelter(shelter_id):
    shelter = next((item for item in shelters if item.get('id') == shelter_id), None)
    if shelter is None:
        abort(404)

    shelters.remove(shelter)
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(shelters, f, ensure_ascii=False, indent=2)

    return_to = request.form.get('next', '')
    if return_to and is_safe_url(return_to):
        return redirect(return_to)
    return redirect(url_for('all_shelters'))


# 指示ボード：住民向けの指示を一覧で確認する
@app.route('/board')
@login_required
def board():
    resident_instructions = [i for i in instructions if i.get('target') == '住民']
    return render_template('board.html', instructions=resident_instructions)

# 検索結果ページ：templates/search_results.html を返す
@app.route('/search_results')
def search_results():
    query = request.args.get('q', '').strip()
    results = (
        search_shelters(query)
        if query
        else filter_shelters(request.args.get('district'), include_unlocated=True)
    )
    return render_template(
        'search_results.html',
        results=results,
        district_groups=group_shelters_by_district(results),
        query=query
    )

# JSON API：/shelters?district=地区名
@app.route('/shelters', methods=['GET'])
def get_shelters():
    results = filter_shelters(request.args.get('district'))

    if not results:
        # 見つからなければエラー JSON を返す
        return jsonify({'error': 'No shelters found'}), 404

    # 見つかったらリストを JSON で返す
    return jsonify(results)

# 気象警報・注意報API
@app.route('/api/weather_warnings')
def api_weather_warnings():
    """気象警報・注意報をJSON形式で返すAPI"""
    return jsonify(get_weather_warnings())

if __name__ == '__main__':
    app.run(debug=True, port=5000)
