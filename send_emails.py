import os
import json
import smtplib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from email.utils import formataddr
import firebase_admin
from firebase_admin import credentials, db
from datetime import datetime, timedelta
from dotenv import load_dotenv

os.environ['PYTHONIOENCODING'] = 'utf8'
load_dotenv()

# Firebase 초기화
cred_dict = json.loads(os.environ.get('FIREBASE_CREDENTIALS'))
cred = credentials.Certificate(cred_dict)
firebase_admin.initialize_app(cred, {
    'databaseURL': 'https://news-385f0-default-rtdb.asia-southeast1.firebasedatabase.app'
})

def get_subscribers():
    """Firebase에서 구독자 목록을 가져옵니다."""
    ref = db.reference('subscribers')
    subscribers = ref.get()
    if subscribers:
        return [data['email'] for data in subscribers.values() if 'email' in data]
    return []

def send_bulk_email(subscribers, subject, html_content):
    """하나의 BCC 그룹(배치)에 이메일 발송."""
    sender_email = os.environ.get('SENDER_EMAIL')
    sender_password = os.environ.get('SENDER_PASSWORD')

    if not subscribers:
        print("⚠️ BCC 수신자가 없습니다.")
        return False

    message = MIMEMultipart('alternative')
    message['Subject'] = Header(subject.encode('utf-8'), 'utf-8').encode()
    message['From'] = formataddr(("KETEP 뉴스브리핑", sender_email))
    message['To'] = sender_email  # Gmail에서는 To 필드 필수
    message['Bcc'] = ','.join(subscribers)

    html_part = MIMEText(html_content, 'html', 'utf-8')
    message.attach(html_part)

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(sender_email, sender_password)
            server.send_message(message)
        print(f"✅ 이메일 발송 성공 ({len(subscribers)}명)")
        return True
    except smtplib.SMTPResponseException as e:
        code = e.smtp_code
        msg = e.smtp_error.decode() if isinstance(e.smtp_error, (bytes, bytearray)) else str(e.smtp_error)
        print(f"⚠️ SMTP 응답 오류: {code} {msg}")
        return False
    except Exception as e:
        print(f"❌ 이메일 발송 실패: {str(e)}")
        return False

def send_emails_in_batches(subscribers, subject, html_content,
                           batch_size=40, base_wait=90, max_retries=3):
    """
    구독자를 여러 배치로 나누어 안전하게 발송.
    - batch_size: 각 BCC 그룹당 인원 수 (기본 40)
    - base_wait: 각 배치 간격 (초 단위, 기본 90초)
    - max_retries: 실패 시 재시도 횟수 (지수 백오프 적용)
    """
    total_batches = (len(subscribers) + batch_size - 1) // batch_size
    success_batches = 0

    for i in range(0, len(subscribers), batch_size):
        batch = subscribers[i:i + batch_size]
        batch_no = (i // batch_size) + 1
        print(f"\n🚀 [배치 {batch_no}/{total_batches}] 발송 시작 ({i+1}~{i+len(batch)}명)")

        # 재시도 로직 (지수 백오프)
        attempt = 0
        sent = False
        while attempt < max_retries and not sent:
            sent = send_bulk_email(batch, subject, html_content)
            if not sent:
                wait_time = base_wait * (2 ** attempt)
                print(f"🔁 재시도 {attempt+1}/{max_retries} — {wait_time}초 후 재시도 예정")
                time.sleep(wait_time)
                attempt += 1

        if sent:
            success_batches += 1
            print(f"✅ [배치 {batch_no}] 발송 완료 ({len(batch)}명)")
        else:
            print(f"❌ [배치 {batch_no}] 모든 재시도 실패")

        # 다음 배치 전 안전 대기
        if batch_no < total_batches:
            print(f"⏸ 다음 배치까지 {base_wait}초 대기 중...")
            time.sleep(base_wait)

    print(f"\n📦 전체 배치 완료: {success_batches}/{total_batches} 성공")
    return success_batches == total_batches

def main():
    # HTML 콘텐츠 로드
    try:
        with open('newsletter.html', 'r', encoding='utf-8') as f:
            html_content = f.read()
    except FileNotFoundError:
        print("❌ newsletter.html 파일을 찾을 수 없습니다.")
        return

    today = datetime.now() + timedelta(hours=9)
    subject = f"{today.strftime('%m월 %d일')} KETEP 뉴스브리핑"

    subscribers = get_subscribers()
    if not subscribers:
        print("⚠️ 구독자가 없습니다.")
        return

    print(f"📧 총 구독자 수: {len(subscribers)}명")

    if send_emails_in_batches(subscribers, subject, html_content):
        print("\n🎉 모든 뉴스레터 발송이 성공적으로 완료되었습니다.")
    else:
        print("\n⚠️ 일부 뉴스레터 발송 중 오류가 발생했습니다.")

if __name__ == "__main__":
    main()
