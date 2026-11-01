# SSH 접속 및 소스 다운로드

[가이드 목록](README.md) · 다음: [드라이버 설치](DRIVER-INSTALL.md)

예시 계정/IP는 `philip@192.168.0.121`이다. 다른 장치에서는 자신의 계정과 주소로 바꾼다.
비밀번호·개인키·GitHub 토큰을 저장소나 명령 로그에 넣지 않는다.

## 1. Windows PC에서 SSH 접속 확인

PowerShell:

```powershell
ssh philip@192.168.0.121
```

기존 SSH 별칭이 설정돼 있다면 `ssh jetson-dvs`를 사용해도 된다.
접속 후 Jetson 터미널에서 확인한다.

```bash
hostname
whoami
uname -m
uname -r
cat /etc/nv_tegra_release
cat /etc/os-release
tr -d '\0' </proc/device-tree/model
printf '\n'
```

재설치 후 서버 호스트 키가 달라졌다면 장치의 실제 호스트 키를 확인한 뒤 갱신한다.
호스트 키 확인과 사용자 공개키 등록은 별개다. 호스트 키 검사를 끄지 않는다.

### 기존 공개키를 재설치한 Jetson에 다시 등록할 때

아래는 해당 PC에 `id_ed25519_jetson_dvs.pub`가 이미 있는 경우의 예다.
다른 키를 사용한다면 파일명을 바꾼다. private key 파일이 아닌 `.pub` 파일만 보낸다.

```powershell
Get-Content "$env:USERPROFILE\.ssh\id_ed25519_jetson_dvs.pub" | ssh philip@192.168.0.121 'umask 077; mkdir -p ~/.ssh; cat >> ~/.ssh/authorized_keys; chmod 700 ~/.ssh; chmod 600 ~/.ssh/authorized_keys'
```

암호화된 키를 비대화형 연결에 사용하려면 에이전트에 등록한다.
서비스가 비활성화돼 있을 때만 관리자 PowerShell에서:

```powershell
Set-Service ssh-agent -StartupType Manual
Start-Service ssh-agent
```

평소 사용자 PowerShell에서:

```powershell
ssh-add "$env:USERPROFILE\.ssh\id_ed25519_jetson_dvs"
ssh -o BatchMode=yes -o IdentitiesOnly=yes -i "$env:USERPROFILE\.ssh\id_ed25519_jetson_dvs" philip@192.168.0.121 "hostname; uname -r"
```

키 암호는 `ssh-add` 프롬프트에 직접 입력한다. 기존 SSH 창에 로그인돼 있어도
새 연결에 인증 상태가 자동으로 공유되지는 않는다.

## 2. Jetson에서 직접 clone 가능한 경우

먼저 같은 이름의 폴더가 있는지 확인한다.

```bash
ls -ld ~/DVS.JS.ORIN.01
```

폴더가 없고 Jetson의 기존 Git 인증으로 private 저장소에 접근할 수 있을 때:

```bash
cd ~
git clone --branch main https://github.com/philipdekim-OnD01/DVS.JS.ORIN.01.git
cd DVS.JS.ORIN.01
git status
git log -1 --oneline
git fsck --full
```

이미 폴더가 있다면 `git status`와 `git remote -v`를 먼저 확인한다.
변경 사항을 지우거나 폴더 위에 덮어쓰지 않는다.

## 3. Jetson GitHub 인증이 없을 때: PC Git bundle 사용

실제 배포에서는 이 경로를 사용했다. Windows에서 Git 인증이 되는 작업 폴더에서 실행한다.
이미 PC clone이 있으면 새 clone 명령을 건너뛰고 그 저장소의 상태를 확인한다.

```powershell
git clone --branch main https://github.com/philipdekim-OnD01/DVS.JS.ORIN.01.git
git -C DVS.JS.ORIN.01 status
```

작업 트리가 깨끗한지 확인한다. 변경 사항이 있으면 여기서 중단해 보존 방법을 먼저 정한다.
기존 clone에서는 현재 브랜치가 다른 이름일 수 있으므로 main으로 전환한 뒤 갱신한다.

```powershell
git -C DVS.JS.ORIN.01 switch main
git -C DVS.JS.ORIN.01 fetch origin main
git -C DVS.JS.ORIN.01 merge --ff-only origin/main
git -C DVS.JS.ORIN.01 bundle create ../dvs-main.bundle main
scp dvs-main.bundle philip@192.168.0.121:dvs-main.bundle
```

충돌이나 변경 사항이 있으면 먼저 정리한다. `--ff-only` 실패를 강제 reset으로 우회하지 않는다.
bundle은 커밋된 Git 객체를 전송하므로 Linux 실행 권한과 심볼릭 링크를 유지한다.

Jetson의 대상 폴더가 없는 상태에서:

```bash
git clone --branch main ~/dvs-main.bundle ~/DVS.JS.ORIN.01
cd ~/DVS.JS.ORIN.01
git remote set-url origin https://github.com/philipdekim-OnD01/DVS.JS.ORIN.01.git
git status
git log -1 --oneline
git fsck --full
git ls-files --stage build.sh scripts/boot-setup.sh tests/check-build.sh
```

세 스크립트의 Git 모드는 `100755`여야 한다. 이후 빌드는 Jetson에서 수행한다.
bundle 전송은 Jetson의 GitHub 인증을 설정해 주는 작업은 아니다.
