"""Collect KMA surface observations for all stations and write a CSV file."""

import argparse
import calendar
import csv
import getpass
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


HOURLY_API_URL = "https://apihub.kma.go.kr/api/typ01/url/kma_sfctm2.php"
HOURLY_PERIOD_API_URL = "https://apihub.kma.go.kr/api/typ01/url/kma_sfctm3.php"
DAILY_PERIOD_API_URL = "https://apihub.kma.go.kr/api/typ01/url/kma_sfcdd3.php"
KST = timezone(timedelta(hours=9))
HOURLY_FIELD_COUNT = 46
DAILY_FIELD_COUNT = 56
CHUNK_DAYS = 30

# Output header, hourly-record field index (zero-based), and missing-value handling.
CSV_FIELDS = (
    ("관측시각(KST)", 0),
    ("관측소번호", 1),
    ("기온(°C)", 11),
    ("강수량_관측시간(mm)", 15),
    ("강수량_일누계(mm)", 16),
    ("강수량_일누계_전문(mm)", 17),
    ("강수강도(mm/h)", 18),
    ("현지기압(hPa)", 7),
    ("해면기압(hPa)", 8),
    ("상대습도(%)", 13),
    ("풍향(도)", 2),
    ("풍속(m/s)", 3),
    ("일사(MJ/m²)", 34),
    ("일조(hr)", 33),
    ("3시간신적설(cm)", 19),
    ("일신적설(cm)", 20),
    ("적설(cm)", 21),
    ("전운량(1/10)", 25),
    ("중하층운량(1/10)", 26),
    ("최저운고(100m)", 27),
    ("운형", 28),
    ("상층운형", 29),
    ("중층운형", 30),
    ("하층운형", 31),
    ("시정(10m 단위)", 32),
    ("지면온도(°C)", 36),
    ("5cm지중온도(°C)", 37),
    ("10cm지중온도(°C)", 38),
    ("20cm지중온도(°C)", 39),
    ("30cm지중온도(°C)", 40),
)
MISSING_VALUES = {"-", "-9", "-9.0", "-9.00", "-99", "-99.0"}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Collect up to three years of KMA station observations into a CSV."
    )
    time_group = parser.add_mutually_exclusive_group()
    time_group.add_argument(
        "--time",
        help="Collect one observation time (YYYYMMDDHHMM) instead of a date range.",
    )
    time_group.add_argument(
        "--start",
        help="Range start in KST (YYYYMMDDHHMM); defaults to three years ago at 00:00.",
    )
    parser.add_argument(
        "--end",
        help="Range end in KST (YYYYMMDDHHMM); defaults to the current hour.",
    )
    parser.add_argument(
        "--station",
        type=int,
        default=0,
        help="KMA station number; use 0 for all stations (default: 0).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output CSV path; defaults to weather_<collection-time>.csv.",
    )
    args = parser.parse_args()

    if args.time:
        try:
            datetime.strptime(args.time, "%Y%m%d%H%M")
        except ValueError:
            parser.error("--time must be a valid date and time in YYYYMMDDHHMM format.")

    if args.end:
        try:
            datetime.strptime(args.end, "%Y%m%d%H%M")
        except ValueError:
            parser.error("--end must be a valid date and time in YYYYMMDDHHMM format.")
    if args.start:
        try:
            datetime.strptime(args.start, "%Y%m%d%H%M")
        except ValueError:
            parser.error("--start must be a valid date and time in YYYYMMDDHHMM format.")
    if args.time and args.end:
        parser.error("--end cannot be used with --time.")

    if args.station < 0:
        parser.error("--station must be zero (all stations) or a positive station number.")

    return args


def parse_kst_time(value):
    return datetime.strptime(value, "%Y%m%d%H%M").replace(tzinfo=KST)


def default_range():
    now = datetime.now(KST).replace(minute=0, second=0, microsecond=0)
    year = now.year - 3
    day = min(now.day, calendar.monthrange(year, now.month)[1])
    start = now.replace(year=year, month=now.month, day=day, hour=0)
    return start, now


def make_chunks(start, end):
    chunks = []
    cursor = start
    while cursor <= end:
        last_day = cursor.date() + timedelta(days=CHUNK_DAYS - 1)
        end_of_chunk = datetime(
            last_day.year, last_day.month, last_day.day, 23, 59, tzinfo=KST
        )
        chunk_end = min(end, end_of_chunk)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(minutes=1)
    return chunks


def fetch_text(url, params, api_key):
    query = dict(params)
    query["authKey"] = api_key
    request = Request(
        "{}?{}".format(url, urlencode(query)),
        headers={"User-Agent": "weather-collector/1.0"},
    )
    try:
        with urlopen(request, timeout=120) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset)
    except HTTPError as error:
        if error.code == 401:
            message = (
                "KMA API rejected the authentication key (HTTP 401). "
                "Check that the key is active and authorized for this API."
            )
        elif error.code == 403:
            message = (
                "KMA API denied access (HTTP 403). "
                "Check your API subscription and permissions."
            )
        else:
            message = "KMA API returned HTTP {}.".format(error.code)
        raise RuntimeError(message) from error
    except URLError as error:
        raise RuntimeError(
            "Could not reach the KMA API: {}".format(error.reason)
        ) from error


def parse_records(
    body,
    expected_fields,
    record_date_length,
    endpoint_name,
    allow_empty=False,
    api_key="",
):
    records = []
    for line_number, line in enumerate(body.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        values = stripped.split()
        if not values[0].isdigit() or len(values[0]) != record_date_length:
            continue
        if len(values) != expected_fields:
            raise RuntimeError(
                "{} response line {} has {} fields; expected {}.".format(
                    endpoint_name, line_number, len(values), expected_fields
                )
            )
        records.append(values)

    if not records:
        if allow_empty:
            return records
        response_lines = [line.strip() for line in body.splitlines() if line.strip()]
        excerpt = next(
            (line for line in response_lines if not line.startswith("#")),
            response_lines[0] if response_lines else "",
        )
        if api_key:
            excerpt = excerpt.replace(api_key, "[redacted]")
        if len(excerpt) > 240:
            excerpt = excerpt[:237] + "..."
        detail = " API response: {}".format(excerpt) if excerpt else ""
        raise RuntimeError(
            "{} API returned no observation rows. Check the key, API access, "
            "time, and station.{}".format(endpoint_name, detail)
        )
    return records


def main():
    args = parse_args()
    api_key = getpass.getpass(
        "기상청 API 인증키를 입력하세요 (Enter: KMA_API_KEY 환경변수 사용): "
    ).strip()
    if not api_key:
        api_key = os.environ.get("KMA_API_KEY", "").strip()
    if not api_key:
        print("Error: an API key is required.", file=sys.stderr)
        return 2

    if args.time:
        start = end = parse_kst_time(args.time)
    else:
        default_start, default_end = default_range()
        start = parse_kst_time(args.start) if args.start else default_start
        end = parse_kst_time(args.end) if args.end else default_end
    if start > end:
        print("Error: the start time must not be after the end time.", file=sys.stderr)
        return 2

    collected_at = datetime.now(KST).strftime("%Y%m%d%H%M%S")
    output_path = args.output or Path("weather_{}.csv".format(collected_at))
    fieldnames = [header for header, _ in CSV_FIELDS]
    fieldnames.append("일최저초상온도(°C)")

    chunks = make_chunks(start, end)
    total_rows = 0
    missing_grass_temperature = 0
    unavailable_daily_chunks = 0
    first_daily_error = None
    temporary_path = None

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            mode="w",
            newline="",
            encoding="utf-8-sig",
            prefix="weather_",
            suffix=".csv.tmp",
            dir=str(output_path.parent),
            delete=False,
        ) as csv_file:
            temporary_path = Path(csv_file.name)
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            writer.writeheader()
            for chunk_number, (chunk_start, chunk_end) in enumerate(chunks, start=1):
                print(
                    "Collecting period {}/{}: {} - {}".format(
                        chunk_number,
                        len(chunks),
                        chunk_start.strftime("%Y%m%d%H%M"),
                        chunk_end.strftime("%Y%m%d%H%M"),
                    ),
                    flush=True,
                )

                daily_params = {
                    "tm1": chunk_start.strftime("%Y%m%d"),
                    "tm2": chunk_end.strftime("%Y%m%d"),
                    "stn": args.station,
                    "help": 0,
                }
                try:
                    daily_body = fetch_text(
                        DAILY_PERIOD_API_URL, daily_params, api_key
                    )
                    daily_records = parse_records(
                        daily_body,
                        DAILY_FIELD_COUNT,
                        8,
                        "Daily",
                        allow_empty=True,
                        api_key=api_key,
                    )
                    if not daily_records:
                        unavailable_daily_chunks += 1
                except RuntimeError as error:
                    daily_records = []
                    unavailable_daily_chunks += 1
                    if first_daily_error is None:
                        first_daily_error = str(error)

                daily_grass_temperature = {
                    (record[0], record[1]): record[17]
                    for record in daily_records
                }

                if args.time:
                    hourly_url = HOURLY_API_URL
                    hourly_params = {
                        "tm": args.time,
                        "stn": args.station,
                        "help": 0,
                    }
                else:
                    hourly_url = HOURLY_PERIOD_API_URL
                    hourly_params = {
                        "tm1": chunk_start.strftime("%Y%m%d%H%M"),
                        "tm2": chunk_end.strftime("%Y%m%d%H%M"),
                        "stn": args.station,
                        "help": 0,
                    }

                hourly_body = fetch_text(hourly_url, hourly_params, api_key)
                hourly_records = parse_records(
                    hourly_body, HOURLY_FIELD_COUNT, 12, "Hourly", api_key=api_key
                )
                for record in hourly_records:
                    grass_temperature = daily_grass_temperature.get(
                        (record[0][:8], record[1]), ""
                    )
                    if grass_temperature in MISSING_VALUES:
                        grass_temperature = ""
                    if not grass_temperature:
                        missing_grass_temperature += 1

                    row = {
                        header: (
                            "" if record[index] in MISSING_VALUES else record[index]
                        )
                        for header, index in CSV_FIELDS
                    }
                    row["일최저초상온도(°C)"] = grass_temperature
                    writer.writerow(row)
                    total_rows += 1

        os.replace(str(temporary_path), str(output_path))
        temporary_path = None
    except (OSError, RuntimeError) as error:
        print("Error: could not complete collection: {}".format(error), file=sys.stderr)
        return 1
    finally:
        if temporary_path and temporary_path.exists():
            try:
                temporary_path.unlink()
            except OSError as error:
                print(
                    "Warning: could not remove temporary file {}: {}".format(
                        temporary_path, error
                    ),
                    file=sys.stderr,
                )

    print("Saved {} observation records to {}".format(total_rows, output_path))
    if unavailable_daily_chunks:
        print(
            "Notice: daily minimum grass temperature was unavailable in {} "
            "period(s).".format(unavailable_daily_chunks),
            file=sys.stderr,
        )
        if first_daily_error:
            print("First daily-data error: {}".format(first_daily_error), file=sys.stderr)
    if missing_grass_temperature:
        print(
            "Notice: daily minimum grass temperature is blank in {} "
            "hourly record(s).".format(missing_grass_temperature),
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
