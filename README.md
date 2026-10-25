# 안전·소방·보건·환경 용품 시장 레이더

네이버 쇼핑과 쿠팡의 안전, 소방, 보건, 환경 용품 인기 순위와 가격(최저가, 평균가, 중앙값, 분포)을 보여주는 무료 홈페이지입니다.
데이터는 GitHub Actions가 **3일마다 자동 수집**하고, GitHub Pages가 **무료로 호스팅**합니다. 서버와 도메인 비용은 0원입니다.

```
safety-radar/
├─ docs/                  ← 홈페이지 (GitHub Pages가 이 폴더를 공개)
│  ├─ index.html
│  └─ data/latest.json, history.json   ← 자동 갱신되는 데이터
├─ collector/
│  ├─ collect.py          ← 수집기 (표준 라이브러리만 사용)
│  ├─ keywords.json       ← 추적 품목 목록 (여기만 고치면 품목 추가/삭제)
│  └─ make_sample.py      ← 샘플 데이터 생성기
└─ .github/workflows/update.yml   ← 3일 주기 자동 실행
```

---

## 1단계: 홈페이지 올리기 (약 10분, 지금 바로 가능)

1. https://github.com 에서 무료 계정을 만듭니다.
2. 오른쪽 위 **+ → New repository**를 누르고 이름을 `safety-radar`로 정한 뒤 **Public**을 선택하고 Create를 누릅니다.
   (무료 계정에서 Pages를 쓰려면 Public이어야 합니다. API 키는 Secrets에 따로 보관되므로 공개되지 않습니다.)
3. 저장소 화면에서 **uploading an existing file**을 누르고 압축을 푼 폴더 안의 내용 전체를 끌어다 놓은 다음 **Commit changes**를 누릅니다.
   - 주의: `.github` 폴더는 숨김 폴더라 드래그할 때 빠질 수 있습니다. 빠졌다면 **Add file → Create new file**에서 파일 이름에
     `.github/workflows/update.yml`을 입력하고 내용을 붙여넣으세요.
4. **Settings → Pages**에서 Source를 `Deploy from a branch`, Branch를 `main` / `/docs`로 정하고 Save를 누릅니다.
5. 1~2분 뒤 `https://<내아이디>.github.io/safety-radar/`가 열립니다. 처음에는 **샘플 데이터**가 표시됩니다.

## 2단계: API 키 등록 (발급 후)

**Settings → Secrets and variables → Actions → New repository secret**에서 아래 이름 그대로 등록합니다.

| Secret 이름 | 발급처 | 필수 여부 | 제공 데이터 |
|---|---|---|---|
| `NAVER_HUB_CLIENT_ID`, `NAVER_HUB_CLIENT_SECRET` | 네이버 클라우드 콘솔 → NAVER API HUB → Application 등록 (사용 API: **검색어트렌드**, **뉴스**, **쇼핑인사이트**) → 인증 정보 | **필수** (둘 중 하나 이상) | 6개월 검색 추세, 품목별 최신 뉴스, 분야별 쇼핑 클릭 추이 |
| `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` | 네이버 개발자센터 → 애플리케이션 등록 (사용 API: **검색**) | 권장 | 가격, 상품 수, 상위 상품 (API HUB에는 쇼핑 상품 검색이 없음) |
| `NAVER_AD_API_KEY`, `NAVER_AD_SECRET_KEY`, `NAVER_AD_CUSTOMER_ID` | 네이버 검색광고 → 도구 → API 사용 관리 | 권장 | 월간 검색량, 인기점수, 경쟁강도 |
| `COUPANG_ACCESS_KEY`, `COUPANG_SECRET_KEY` | 쿠팡 파트너스 → 추가기능 → Open API (승인 조건 있음) | 선택 | 쿠팡 가격, 로켓배송 비율 |

> NAVER API HUB 키는 개발자센터 키와 서로 호환되지 않습니다. 반드시 위 이름 그대로 따로 등록하세요.
> 쇼핑인사이트는 `collector/keywords.json`의 `shop_categories`에 네이버 쇼핑 카테고리 코드(cat_id)를 넣어야 수집됩니다.

등록이 끝나면 **Actions 탭 → 데이터 자동 업데이트 → Run workflow**를 눌러 첫 수집을 실행합니다. 2~3분 뒤 실제 데이터로 바뀝니다.

## 자동 업데이트 방식

- 매일 오전 6시경(한국 시간)에 확인하고, 마지막 수집 뒤 70시간 이상 지났으면 수집합니다. 그래서 결과적으로 **3일마다** 갱신됩니다.
  (크론의 `*/3`은 월이 바뀔 때 간격이 틀어져서 이 방식을 썼습니다.)
- 일부 API가 실패하면 그 항목은 이전 데이터를 유지하고, 화면 상단에 경고가 표시됩니다.
- 쿠팡 검색 API는 시간당 호출 제한이 있어 실행할 때마다 오래된 품목부터 10개씩 순환 갱신합니다 (`COUPANG_MAX_PER_RUN`).
- 저장소에 60일 동안 활동이 없으면 GitHub가 예약 실행을 멈출 수 있습니다. 자동 커밋으로 대부분 유지되지만, 멈추면 Actions 탭에서 다시 Enable을 누르면 됩니다.

## AI 수요 예측 (1·2·3개월 후 추천)

- 수집할 때마다 품목별 **최근 3년 월간 검색 관심도**를 받아 계절지수(월별 성수기·비수기 패턴)를 계산합니다.
- 계절성을 뺀 최근 수준 × 전년 대비 추세 × 대상 월 계절지수 × 최근 1주 뉴스량 보정으로 1·2·3개월 후 수요를 예측합니다.
- 추천 점수 = 예상 수요 규모 55% + 이번 달 대비 증가율 35% + 뉴스 관심 10%. 신뢰도는 과거 같은 달의 패턴이 얼마나 일정했는지로 판단합니다.
- 외부 AI 유료 API 없이 GitHub Actions 안에서 계산하므로 추가 비용이 없습니다.

## 품목 추가/수정

`collector/keywords.json`에 아래 형식으로 한 줄을 추가하면 다음 수집부터 반영됩니다.

```json
{"id": "welding-mask", "group": "안전", "sub": "개인보호구", "name": "용접면", "keyword": "용접면",
 "exclude": ["필름"], "cert": "용접용 보안면 안전인증(KCs)", "memo": "자동차광 제품 수요 증가"}
```

`group`은 `안전`, `소방`, `보건`, `환경` 중 하나로 넣어야 합니다.

## 지표 해석 시 주의

- 네이버와 쿠팡 모두 실제 판매량은 공개하지 않습니다. 인기점수는 **검색량과 관심도를 바탕으로 한 수요 추정치**입니다.
- 쿠팡 데이터는 공식 파트너스 API로만 수집합니다. 페이지 크롤링은 약관 위반이라 쓰지 않습니다.
- 인증 정보는 참고용입니다. 판매 전에 최신 법령(산업안전보건법, 소방시설법 등)을 꼭 확인하세요.
