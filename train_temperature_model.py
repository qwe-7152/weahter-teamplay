"""Train and evaluate a time-based linear model for KMA hourly temperature."""

import argparse
import csv
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

try:
    import numpy as np
    from sklearn.linear_model import LinearRegression
    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
except ImportError as error:
    raise SystemExit(
        "Install the model dependencies with: python -m pip install numpy scikit-learn"
    ) from error


KST_FORMAT = "%Y%m%d%H%M"
TRAIN_START = datetime(2023, 1, 1)
TRAIN_END = datetime(2026, 9, 30, 23)
FORECAST_START = datetime(2026, 10, 1)
FORECAST_END = datetime(2026, 12, 31, 23)
TIME_COLUMN = "관측시각(KST)"
STATION_COLUMN = "관측소번호"
TEMPERATURE_COLUMN = "기온(°C)"
MISSING_VALUES = {"", "-", "-9", "-9.0", "-9.00", "-99", "-99.0"}
FEATURE_NAMES = (
    "trend_years",
    "daily_sin",
    "daily_cos",
    "annual_sin",
    "annual_cos",
    "semiannual_sin",
    "semiannual_cos",
)


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Train on hourly temperature through September 2026, "
            "evaluate available October-November observations, and forecast "
            "through December 2026."
        )
    )
    parser.add_argument("--input", type=Path, required=True, help="Weather CSV input.")
    parser.add_argument(
        "--station",
        default="108",
        help="Station number to model (default: 108, Seoul).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("temperature_forecast_108_202610-202612.csv"),
        help="CSV file for holdout predictions and future forecasts.",
    )
    parser.add_argument(
        "--metrics",
        type=Path,
        default=Path("temperature_model_metrics_108.json"),
        help="JSON file for training and holdout metrics.",
    )
    return parser.parse_args()


def make_features(times):
    origin = datetime(2023, 1, 1)
    result = []
    for timestamp in times:
        elapsed_days = (timestamp - origin).total_seconds() / 86400
        hour_fraction = (
            timestamp.hour
            + timestamp.minute / 60
            + timestamp.second / 3600
        ) / 24
        daily_phase = 2 * np.pi * hour_fraction
        year_length = 366 if _is_leap_year(timestamp.year) else 365
        annual_phase = (
            2
            * np.pi
            * (timestamp.timetuple().tm_yday - 1 + hour_fraction)
            / year_length
        )
        result.append(
            (
                elapsed_days / 365.2425,
                np.sin(daily_phase),
                np.cos(daily_phase),
                np.sin(annual_phase),
                np.cos(annual_phase),
                np.sin(2 * annual_phase),
                np.cos(2 * annual_phase),
            )
        )
    return np.asarray(result, dtype=float)


def _is_leap_year(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def read_observations(path, station):
    observations = {}
    with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        required_columns = {TIME_COLUMN, STATION_COLUMN, TEMPERATURE_COLUMN}
        missing_columns = required_columns.difference(reader.fieldnames or [])
        if missing_columns:
            raise ValueError(
                "Input CSV is missing required columns: {}".format(
                    ", ".join(sorted(missing_columns))
                )
            )

        for line_number, row in enumerate(reader, start=2):
            if row[STATION_COLUMN].strip() != station:
                continue
            timestamp_text = row[TIME_COLUMN].strip()
            temperature_text = row[TEMPERATURE_COLUMN].strip()
            if temperature_text in MISSING_VALUES:
                continue
            timestamp = datetime.strptime(timestamp_text, KST_FORMAT)
            if timestamp in observations:
                raise ValueError(
                    "Duplicate station/time record at CSV line {}.".format(
                        line_number
                    )
                )
            try:
                temperature = float(temperature_text)
            except ValueError as error:
                raise ValueError(
                    "Invalid temperature at CSV line {}.".format(line_number)
                ) from error
            observations[timestamp] = temperature
    return observations


def write_predictions(path, station, timestamps, actuals, predictions):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(
            (
                TIME_COLUMN,
                STATION_COLUMN,
                "실제기온(°C)",
                "예측기온(°C)",
                "오차_실제-예측(°C)",
                "구분",
            )
        )
        for timestamp, prediction in zip(timestamps, predictions):
            actual = actuals.get(timestamp)
            if actual is None:
                actual_text = ""
                residual_text = ""
                split = "미래예측"
            else:
                actual_text = "{:.3f}".format(actual)
                residual_text = "{:.3f}".format(actual - prediction)
                split = "검증"
            writer.writerow(
                (
                    timestamp.strftime(KST_FORMAT),
                    station,
                    actual_text,
                    "{:.3f}".format(float(prediction)),
                    residual_text,
                    split,
                )
            )


def main():
    args = parse_args()
    if not args.input.is_file():
        print("Error: input CSV does not exist: {}".format(args.input), file=sys.stderr)
        return 2

    try:
        observations = read_observations(args.input, args.station)
    except (OSError, ValueError) as error:
        print("Error: {}".format(error), file=sys.stderr)
        return 1

    training = {
        timestamp: temperature
        for timestamp, temperature in observations.items()
        if TRAIN_START <= timestamp <= TRAIN_END
    }
    if not training:
        print("Error: no training temperatures were found.", file=sys.stderr)
        return 1

    train_times = sorted(training)
    train_values = np.asarray([training[timestamp] for timestamp in train_times])
    model = LinearRegression()
    model.fit(make_features(train_times), train_values)

    forecast_times = []
    timestamp = FORECAST_START
    while timestamp <= FORECAST_END:
        forecast_times.append(timestamp)
        timestamp += timedelta(hours=1)

    predictions = model.predict(make_features(forecast_times))
    test_times = [
        timestamp
        for timestamp in forecast_times
        if timestamp in observations
    ]
    if not test_times:
        print(
            "Error: no observed temperatures are available in the holdout period.",
            file=sys.stderr,
        )
        return 1

    actual_values = np.asarray([observations[timestamp] for timestamp in test_times])
    test_predictions = model.predict(make_features(test_times))
    rmse = float(np.sqrt(mean_squared_error(actual_values, test_predictions)))
    metrics = {
        "station": args.station,
        "target": "hourly air temperature (TA, degrees C)",
        "model": "ordinary least squares linear regression",
        "features": list(FEATURE_NAMES),
        "requested_training_period": [
            TRAIN_START.strftime(KST_FORMAT),
            TRAIN_END.strftime(KST_FORMAT),
        ],
        "actual_training_period": [
            train_times[0].strftime(KST_FORMAT),
            train_times[-1].strftime(KST_FORMAT),
        ],
        "training_observations": len(train_times),
        "requested_holdout_period": [
            FORECAST_START.strftime(KST_FORMAT),
            FORECAST_END.strftime(KST_FORMAT),
        ],
        "actual_holdout_period": [
            test_times[0].strftime(KST_FORMAT),
            test_times[-1].strftime(KST_FORMAT),
        ],
        "holdout_observations": len(test_times),
        "holdout_mae_c": float(mean_absolute_error(actual_values, test_predictions)),
        "holdout_rmse_c": rmse,
        "holdout_r2": float(r2_score(actual_values, test_predictions)),
        "december_has_actual_observations": any(
            timestamp.year == 2026 and timestamp.month == 12
            for timestamp in observations
        ),
        "forecast_csv": str(args.output),
    }

    try:
        write_predictions(
            args.output,
            args.station,
            forecast_times,
            observations,
            predictions,
        )
        args.metrics.parent.mkdir(parents=True, exist_ok=True)
        args.metrics.write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        print("Error: could not write model results: {}".format(error), file=sys.stderr)
        return 1

    print("Station: {}".format(args.station))
    print(
        "Training: {} - {} ({} observations)".format(
            metrics["actual_training_period"][0],
            metrics["actual_training_period"][1],
            len(train_times),
        )
    )
    print(
        "Holdout: {} - {} ({} observations)".format(
            metrics["actual_holdout_period"][0],
            metrics["actual_holdout_period"][1],
            len(test_times),
        )
    )
    print(
        "Holdout MAE: {:.3f} C; RMSE: {:.3f} C; R2: {:.3f}".format(
            metrics["holdout_mae_c"],
            metrics["holdout_rmse_c"],
            metrics["holdout_r2"],
        )
    )
    print("Predictions: {}".format(args.output))
    print("Metrics: {}".format(args.metrics))
    print(
        "December actuals are not present in the input; December values are "
        "forecasts only."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
