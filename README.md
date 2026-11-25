# KMA 관측자료 수집 및 기온 예측

기상청 API 허브의 종관기상관측(ASOS) 자료를 CSV로 수집하고, 서울 관측소(108)의 시간별 기온을 선형회귀로 학습·검증하는 Python 프로젝트입니다.

## 주요 기능

- 전체 관측소(`stn=0`) 또는 특정 관측소의 시간 관측자료 수집
- 기본 최근 3년 기간을 30일씩 나누어 요청하고 CSV 한 파일로 저장
- 기온, 강수, 기압, 습도, 풍향·풍속, 일사·일조, 적설, 구름, 시정, 지면·지중온도 정리
- ASOS 일자료의 일 최저 초상온도(`TG_MIN`)를 관측 날짜와 관측소별로 연결
- 시간별 기온 선형회귀 학습, 관측값이 있는 기간의 홀드아웃 평가, 이후 시간 예측

## 준비 사항

- Python 3
- 기상청 API 허브 계정과 API 인증키
- 모델 학습 기능 사용 시 `numpy`, `scikit-learn`

모델 의존성 설치:

```powershell
python -m pip install numpy scikit-learn
```

## 관측자료 수집

프로젝트 폴더에서 실행하면 API 인증키를 입력하라는 메시지가 표시됩니다. 입력을 비워 두면 `KMA_API_KEY` 환경변수를 사용합니다.

```powershell
python .\collect_weather.py
```

기본 동작은 전체 관측소의 최근 3년 자료 수집이며, 결과는 `weather_<수집시각>.csv`로 저장됩니다. 기상청 기간조회 API의 1회 최대 31일 제한에 맞춰 30일 단위로 나누어 요청합니다. 수집 중 각 구간이 출력되며, 긴 기간의 전체 관측소 수집은 시간이 걸리고 CSV 파일이 커질 수 있습니다.

### 수집 옵션

```powershell
# 특정 관측소의 특정 시각 자료
python .\collect_weather.py --time 202611251200 --station 108 --output .\weather_108.csv

# 특정 기간의 특정 관측소 자료 (KST, YYYYMMDDHHMM)
python .\collect_weather.py --start 202301010000 --end 202609302300 --station 108 --output .\weather_108.csv
```

- `--time`: 한 시각만 조회합니다. `--start`와 함께 사용할 수 없습니다.
- `--start`, `--end`: 조회 구간의 시작·종료 시각입니다. 생략하면 최근 3년 구간을 기본값으로 사용합니다.
- `--station`: 관측소 번호입니다. `0`은 전체 관측소이며 기본값입니다.
- `--output`: 결과 CSV 경로입니다.

환경변수로 인증키를 사용하려면:

```powershell
$env:KMA_API_KEY = "발급받은_인증키"
python .\collect_weather.py
```

인증키는 코드나 저장소에 넣거나 공개 채팅·파일에 공유하지 마세요. 인증키가 거부되면 API 허가 및 키 활성 상태를 확인하세요.

## 기온 모델 학습 및 예측

현재 모델 스크립트는 서울 108번 관측소의 시간별 기온을 대상으로 합니다. 학습 구간은 2023-01-01부터 2026-09-30까지로 설정되어 있으며, 2026-10-01부터 2026-12-31까지 시간별 예측값을 생성합니다. 예측 변수는 시간 추세와 일·연중 주기의 사인·코사인 항입니다.

```powershell
python .\train_temperature_model.py --input .\weather_20261125122949.csv
```

기본 출력:

- `temperature_forecast_108_202610-202612.csv`: 실제값(있는 경우), 예측값, 오차, 검증/미래예측 구분
- `temperature_model_metrics_108.json`: 학습·검증 범위와 MAE, RMSE, R²

다른 파일과 출력 위치를 지정할 수도 있습니다:

```powershell
python .\train_temperature_model.py `
  --input .\weather_108.csv `
  --station 108 `
  --output .\temperature_forecast.csv `
  --metrics .\temperature_metrics.json
```

### 현재 데이터셋 기준 결과와 한계

현재 확인된 입력 데이터(`weather_20261125122949.csv`)는 2023-11-25 00시부터 2026-11-25 12시까지입니다. 따라서 요청한 학습 시작일인 2023년 1월부터 11월 24일까지의 자료는 입력 파일에 없습니다. 실제 학습은 2023-11-25부터 2026-09-30까지 24,935건으로 수행되었습니다.

2026년 10월 1일부터 11월 25일 12시까지 1,333건으로 홀드아웃 검증한 결과:

- MAE: 3.371 °C
- RMSE: 4.004 °C
- R²: 0.663

입력 데이터에 2026년 12월 실제 관측값이 없으므로 12월 행은 예측값만 있으며 정답 비교 대상이 아닙니다. 이 선형회귀는 계절·일주기와 추세를 기준으로 한 참고 모델이며, 특정 날의 기상 현상을 예보하는 용도로 사용할 수 없습니다.

## 파일 구성

| 파일 | 설명 |
|---|---|
| `collect_weather.py` | 기상청 ASOS 관측자료 수집 및 CSV 저장 |
| `train_temperature_model.py` | 기온 모델 학습, 홀드아웃 평가, 예측 CSV 생성 |
| `weather_*.csv` | 수집한 관측자료 |
| `temperature_forecast_*.csv` | 홀드아웃 및 미래 예측값 |
| `temperature_model_metrics_*.json` | 모델 평가 지표 |

## 공식 자료

- [기상청 API 허브](https://apihub.kma.go.kr/)
- [종관기상관측 시간자료 조회 API](https://apihub.kma.go.kr/api/typ01/url/kma_sfctm2.php)
- [종관기상관측 시간자료 기간조회 API](https://apihub.kma.go.kr/api/typ01/url/kma_sfctm3.php)
