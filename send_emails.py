import os
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import firebase_admin
from firebase_admin import credentials
from firebase_admin import db
from datetime import datetime, timedelta
import time
from dotenv import load_dotenv

load_dotenv()
# Firebase 초기화
cred_dict = json.loads(os.environ.get('FIREBASE_CREDENTIALS'))
cred = credentials.Certificate(cred_dict)
firebase_admin.initialize_app(cred, {
    'databaseURL': 'https://news-385f0-default-rtdb.asia-southeast1.firebasedatabase.app'  # Firebase 데이터베이스 URL로 변경
})

def get_subscribers():
    """Firebase에서 구독자 목록을 가져옵니다."""
    ref = db.reference('subscribers')
    subscribers = ref.get()
    if subscribers:
        return [data['email'] for data in subscribers.values()]
    return []

def send_bulk_email(subscribers, subject, html_content):
    """BCC를 사용하여 모든 구독자에게 한 번에 이메일을 발송합니다."""
    if not subscribers:
        print("구독자가 없습니다.")
        return False

    sender_email = os.environ.get('SENDER_EMAIL')
    sender_password = os.environ.get('SENDER_PASSWORD')

    # 메일 기본 설정
    message = MIMEMultipart('alternative')
    message['Subject'] = subject
    message['From'] = sender_email
    message['To'] = 'kimyh@ketep.re.kr'#sender_email  # 발신자 주소를 수신자로 설정
    #message['Bcc'] = ', '.join(subscribers)  # 모든 구독자를 BCC로 설정

    html_part = MIMEText(html_content, 'html', 'utf-8')
    message.attach(html_part)

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(sender_email, sender_password)
            server.send_message(message)
        print(f"이메일 발송 성공: 총 {len(subscribers)}명의 구독자에게 발송됨")
        return True
    except Exception as e:
        print(f"이메일 발송 실패: {str(e)}")
        return False

def send_emails_in_batches(subscribers, subject, html_content, batch_size=80):
    """구독자를 그룹으로 나누어 발송합니다."""
    total_batches = (len(subscribers) + batch_size - 1) // batch_size  # 총 배치 수 계산
    success_count = 0
    
    for i in range(0, len(subscribers), batch_size):
        batch = subscribers[i:i + batch_size]
        current_batch = (i // batch_size) + 1
        
        print(f"배치 {current_batch}/{total_batches} 발송 시작 (구독자 {i+1}~{i+len(batch)}명)")
        
        if send_bulk_email(batch, subject, html_content):
            success_count += 1
            print(f"배치 {current_batch} 발송 성공: {i+1}~{i+len(batch)}번째 구독자")
        else:
            print(f"배치 {current_batch} 발송 실패: {i+1}~{i+len(batch)}번째 구독자")
        
        if current_batch < total_batches:
            print("다음 배치 발송 전 30초 대기...")
            time.sleep(30)  # 다음 배치 전 30초 대기
    
    return success_count == total_batches

def main():
    try:
        with open('newsletter.html', 'r', encoding='utf-8') as f:
            html_content = f.read()
    except FileNotFoundError:
        print("newsletter.html 파일을 찾을 수 없습니다.")
        return

    # 현재 날짜로 제목 생성
    today = datetime.now()
    today_kst = today + timedelta(hours=9)
    date_str = today_kst.strftime("%m월 %d일")
    subject = f"{date_str} KETEP 뉴스브리핑"

    # 구독자 목록 가져오기
    subscribers = get_subscribers()
    
    if not subscribers:
        print("구독자가 없습니다.")
        return
        
    print(f"총 구독자 수: {len(subscribers)}명")
    
    # 배치 단위로 이메일 발송
    if send_emails_in_batches(subscribers, subject, html_content):
        print("모든 뉴스레터 발송이 완료되었습니다.")
    else:
        print("뉴스레터 발송 중 오류가 발생했습니다.")

if __name__ == "__main__":
    main()
