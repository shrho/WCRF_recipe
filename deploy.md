# WCRF Recipe Book — 외부 호스팅 배포 가이드 (Render.com 권장)

이 앱은 Flask + SQLite 기반이며, 레시피 이미지는 앱 내 `static/vault-images/`에 복사되어 포함되므로 외부 서버에서도 이미지가 정상적으로 표시됩니다.

## 1. 저장소 준비

앱 폴더를 Git 저장소로 만들고 GitHub에 푸시합니다.

```bash
cd /Users/shrho/wcrf-recipe-app
git init
git add .
git commit -m "Initial recipe book with auth, boards, and social features"
# GitHub 저장소 생성 후 remote 추가 및 푸시
git remote add origin <당신-GitHub-저장소-URL>
git branch -M main
git push -u origin main
```

## 2. Render에 배포

1. [Render](https://render.com)에 가입/로그인합니다.
2. "New +" → "Web Service" 선택 → GitHub 저장소 연결.
3. 아래 설정 확인:
   - Runtime: Python
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn app:app`
4. 환경 변수 `SECRET_KEY`를 임의의 임의 문자열로 설정합니다(또는 render.yaml이 자동 생성).
5. "Create Web Service" 클릭.

배포가 완료되면 Render가 부여한 URL(예: `https://wcrf-recipe-book.onrender.com`)로 외부에서 접속할 수 있습니다.

## 3. 최초 관리자 계정

앱 시작 시 기본 관리자 계정이 자동 생성됩니다.

- 아이디: `admin`
- 비밀번호: `admin123`

로그인 후 비밀번호를 변경하고, `/admin`에서 회원 관리를 하세요.

## 대안 호스팅

- **Railway**: `railway up`으로 배포, 시작 명령은 `gunicorn app:app`
- **Fly.io**: `fly launch` 후 `fly deploy`
- **PythonAnywhere**: Flask 웹 앱으로 수동 구성, WSGI 파일에서 `app` 객체 참조

## 참고

- 이미지 경로: `/image/<파일명>`은 우선 `static/vault-images/`에서 제공하며, 없으면 개발용 로컬 볼트를 fallback으로 탐색합니다.
- 데이터베이스 `memos.db`는 Render의 디스크에 저장됩니다(무료 플랜은 재배포 시 데이터 초기화될 수 있음. 중요한 데이터는 외부 DB 연동을 고려하세요).
