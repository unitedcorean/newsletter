import os
from dotenv import load_dotenv
import trafilatura
from trafilatura import fetch_url, extract
from gnews import GNews
from langchain_openai import ChatOpenAI
from googlenewsdecoder import new_decoderv1
from langchain_core.output_parsers import StrOutputParser
from langchain.prompts import PromptTemplate
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from kiwipiepy import Kiwi
from newspaper import Article
from datetime import datetime
import pandas as pd
import requests
import locale
import ssl
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import schedule
import time

class NewsletterGenerator:
    def __init__(self):
        load_dotenv()
        locale.setlocale(locale.LC_TIME, 'ko_KR.UTF-8')
        self.keyword_groups = [
            # {
            #     "topic": "에기평",
            #     "keywords": ["에기평 OR 에너지기술평가원 OR 원장이승재 OR KETEP"],
            #     "count": 10
            # },
            {
                "topic": "산업부",
                "keywords": ["(산업부 OR 산업통상자원부 OR 산자부) (에너지)"],
                "count": 10
            }
            # {
            #     "topic": "원자력",
            #     "keywords": ["원자력", "원자로", "원전", "방폐물", "SMR", "핵융합", "핵연료"],
            #     "count": 10
            # },
            # { 
            #     "topic": "수소, 연료전지",
            #     "keywords": ["수소", "연료전지", "수전해", "개질"],
            #     "count": 10
            # },
            # {
            #     "topic": "태양광",
            #     "keywords": ["태양광", "결정질실리콘", "무기박막", "유기박막", "탠덤태양전지", "페로브스카이트"],
            #     "count": 10
            # },
            # {
            #     "topic": "풍력",
            #     "keywords": ["풍력", "해상변전소"],
            #     "count": 10
            # },
            # {
            #     "topic": "전력",
            #     "keywords": ["화력발전", "터빈", "혼소", "송배전", "전력전자", "전력계통", "전력시장", "그리드"],
            #     "count": 10
            # },
            # {
            #     "topic": "에너지수요관리",
            #     "keywords": ["히트펌프", "전동기", "유체기기", "전력변환", "에너지효율", "수요자원", "수요반응", "VPP"],
            #     "count": 10
            # },     
            # {
            #     "topic": "자원, CCUS",
            #     "keywords": ["온실가스 OR 자원순환 OR CCS OR CCU OR 지중저장 OR 탄소포집 OR 탄소저장 OR 재자원화 OR (천연가스 OR 유가스 OR 핵심광물) (개발 OR 운송)"],
            #     "count": 10
            # },      
            # {
            #     "topic": "에너지저장장치",
            #     "keywords": ["ESS", "열저장", "기계식저장", "압축공기", "카르노배터리"],
            #     "count": 10
            # },
            # {
            #     "topic": "에너지안전",
            #     "keywords": ["에너지안전", "가스안전", "전기안전", "ESS안전", "안전성평가", "재해예방"],
            #     "count": 10
            # },
            # {
            #     "topic": "기술사업화",
            #     "keywords": ["기후테크", "에너지벤처", "에너지스타트업", "에너지기술", "에너지R&D"],
            #     "count": 10
            # }
        ]
        
        # 검색 기간 설정
        self.period = "일단위"  # "일단위", "주단위", "월단위" 중 선택
        self.start_date = None
        self.end_date = None
        
    def save_to_csv(self, news_list):
        try:
            # 현재 날짜를 파일명에 포함
            current_dir = os.getcwd()
            # today = datetime.now().strftime("%Y%m%d")
            # 데이터프레임 생성을 위한 리스트 준비
            data = []
            for news in news_list:
                data.append({
                    'keyword': news['search_keyword'],
                    'title': news['title'],
                    'press': news['press'],
                    'date': news['date'],
                    'content': news['content'],
                    'original_url': news['original_url'],
                    'image_url' : news['image_url']
                })
            
            # 데이터프레임 생성
            df = pd.DataFrame(data)
            
            # CSV 파일로 저장 (현재 작업 디렉토리에 저장)
            file_path = os.path.join(current_dir, 'newsletter.csv')
            
            # 파일이 이미 존재하는 경우 추가 모드로 저장
            if os.path.exists(file_path):
                df.to_csv(file_path, mode='a', header=False, index=False, encoding='utf-8-sig')
            else:
                df.to_csv(file_path, index=False, encoding='utf-8-sig')
                
            return file_path
        except Exception as e:
            print(f"CSV 파일 저장 중 오류 발생: {str(e)}")
            return None

    def get_news(self, keyword):
        if self.period:  # 기간 단위로 설정한 경우
            if self.period == "일단위":
                when = "1d"
            elif self.period == "주단위":
                when = "7d"
            elif self.period == "월단위":
                when = "30d"
        else:  # 날짜로 직접 설정한 경우
            # start_date와 end_date를 문자열로 변환 (YYYY-MM-DD 형식)
            start_date = (self.start_date.year, self.start_date.month, self.start_date.day)
            end_date = (self.end_date.year, self.end_date.month, self.end_date.day)
        
        try:
            if self.period:  # 기간 단위로 설정한 경우
                gnews = GNews(language='ko', country='KR', period=when, max_results=10)
            else:  # 날짜로 직접 설정한 경우
                gnews = GNews(language='ko', country='KR', start_date=start_date, end_date=end_date, max_results=10)
            # GNews 모듈을 사용하여 뉴스 검색
            news_items = gnews.get_news(keyword)
            news_list = []
            interval_time = 5
            
            for item in news_items:
                try:
                    title = item['title']  # 제목 추출
                    source_url = item['url']  # 원본 URL 추출
                    decoded_url = new_decoderv1(source_url, interval=interval_time)
                    original_url = decoded_url['decoded_url']
                    press = item['publisher']['title']  # 출처 추출
                    date = item['published date']  # 날짜 추출
                    
                    # trafilatura를 사용하여 뉴스 본문 수집
                    downloaded = trafilatura.fetch_url(original_url)
                    content = trafilatura.extract(downloaded)
                    
                    if not content:
                        continue
                    # SSL 검증을 비활성화하여 이미지 URL 추출
                    try:
                        # 기존 SSL 컨텍스트 저장
                        original_context = ssl._create_default_https_context
                        # SSL 검증 비활성화
                        ssl._create_default_https_context = ssl._create_unverified_context
                        
                        # newspaper3k로 이미지 URL 추출
                        article = Article(original_url)
                        article.download()
                        article.parse()
                        main_image = article.top_image
                        
                        # http를 https로 변환
                        if main_image and main_image.startswith('http:'):
                            main_image = main_image.replace('http:', 'https:', 1)
                    finally:
                        # SSL 컨텍스트 복원
                        ssl._create_default_https_context = original_context
                    
                    news_list.append({
                        'title': title,
                        'original_url': original_url,
                        'press': press,
                        'date': date,
                        'content': content,
                        'summary': '',
                        'image_url': main_image if 'main_image' in locals() else ''
                    })

                except Exception as e:
                    print(f"개별 뉴스 처리 실패: {str(e)}")
                    continue  # 실패한 뉴스는 건너뛰고 계속 진행

            return news_list
        except Exception as e:
            print(f"뉴스 검색 실패: {str(e)}")
            return []
    
    def analyze_morphology(self, text):
        kiwi = Kiwi()    
        tokens = kiwi.analyze(text)
    # 첫 번째 분석 결과만 사용하며, 명사(NNG, NNP), 동사(VV)만 추출
        words = [token[0] for token in tokens[0][0] if token[1] in ('NNG', 'NNP', 'VV')]
        return ' '.join(words)

    def group_articles_with_similarity(self, articles):
        # 유효한 텍스트가 있는 기사만 필터링
        valid_articles = [article for article in articles if article.get('content')]
        if not valid_articles:
            return [[article] for article in articles]  # 각 기사를 개별 그룹으로 반환

        # 유효한 텍스트에 대해서만 형태소 분석 수행
        texts = [self.analyze_morphology(article['content']) for article in valid_articles]
        
        # texts가 비어 있는 경우 처리
        if not texts:
            return [[article] for article in articles]

        vectorizer = TfidfVectorizer(stop_words='english')
        X = vectorizer.fit_transform(texts)

        # 코사인 유사도 계산
        similarity_matrix = cosine_similarity(X)

        # 그룹화 로직
        groups = []
        visited = set()

        # valid_articles의 인덱스 범위 내에서만 반복
        for i in range(len(valid_articles)):
            if i in visited:
                continue
            
            group = [valid_articles[i]]
            visited.add(i)

            for j in range(i + 1, len(valid_articles)):
                if j not in visited and similarity_matrix[i, j] >= 0.6:
                    group.append(valid_articles[j])
                    visited.add(j)

            groups.append(group)

        # 유효하지 않은 기사들을 개별 그룹으로 추가
        invalid_articles = [article for article in articles if article not in valid_articles]
        for article in invalid_articles:
            groups.append([article])

        return groups

    def summarize_content(self, content):
        if not content:  # content가 비어있을 경우
            return "내용이 없습니다."

        try:
            api_key = os.getenv("OPENAI_API_KEY")
            prompt = PromptTemplate.from_template("{topic}을 간결하게 3줄로 요약해주세요. 각 문장은 줄바꿈해주세요.")
            model = ChatOpenAI(model="gpt-4o-mini", api_key=api_key)  # API 키 추가
            chain = prompt | model | StrOutputParser()
            input = {"topic" : content}
            answer = chain.invoke(input)
            return answer
        except Exception:
            return "요약 생성 실패"
    
    def get_weather_info(self):
        try:
            # WeatherAPI.com API 사용
            api_key = os.getenv("WEATHER_API_KEY")  # WeatherAPI.com의 API 키로 변경 필요
            city = "seoul"
            url = f"http://api.weatherapi.com/v1/forecast.json?key=0e50741b3c7142e9b2773529250101&q={city}&days=1&aqi=no"
            
            response = requests.get(url)
            data = response.json()
            
            if response.status_code == 200:
                forecast = data['forecast']['forecastday'][0]['day']
                temp_min = round(forecast['mintemp_c'])
                temp_max = round(forecast['maxtemp_c'])
                condition = data['forecast']['forecastday'][0]['day']['condition']
                icon_url = f"https:{condition['icon']}"  # WeatherAPI.com은 이미 완전한 URL을 제공
                
                return {
                    'temp_min': temp_min,
                    'temp_max': temp_max,
                    'icon_url': icon_url
                }
            return None
        except Exception as e:
            print(f"날씨 정보 가져오기 실패: {str(e)}")
            return None
        
    def generate_html(self):
        today = datetime.now()
        date_str = today.strftime("%Y년 %m월 %d일(%a)")
        all_news = []
        
        weather_info = self.get_weather_info()
        
        newsletter_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
        </head>
        <body>
            <div style="width: 850px; margin: 0 auto;">
            <div style="background: #ffffff; border-radius: 4px 4px 0px 0px; border: 1px solid #e6e6e6; padding: 30px; display: flex; flex-direction: column; gap: 10px; align-items: flex-start; justify-content: flex-start; position: relative; flex-shrink: 0;">
                <div style="display: flex; flex-direction: column; gap: 10px; align-items: flex-start; justify-content: flex-start; align-self: stretch; position: relative; flex-shrink: 0;">
                    <div style="align-self: stretch; flex-shrink: 0; height: 86px; position: relative;">
                        <div style="color: #292929; text-align: left; font-family: 'Arial'; font-size: 57px; letter-spacing: -0.05em; font-weight: 700; position: absolute; right: 4.84%; left: 0%; width: 95.16%; bottom: 0%; top: 0%; height: 100%;">KETEP NEWSLETTER</div>
                    </div>
                    <div style="border: 0px; padding: 0px; display: flex; flex-direction: row; align-items: center; justify-content: space-between; align-self: stretch; position: relative; flex-shrink: 0;">
                        <div style="color: #292929; text-align: left; font-family: 'Arial'; font-size: 13px; font-weight: 700; text-transform: uppercase;">{date_str}
                            <img src="{weather_info['icon_url']}" style="height: 4em; vertical-align: middle;">
                            <span>{weather_info['temp_min']}℃ ~ {weather_info['temp_max']}℃</span>
                        </div>
                    </div>
                </div>
            </div>
        """
        
        for group_idx, group in enumerate(self.keyword_groups):
            if not group["keywords"] or not group["keywords"][0].strip():
                continue
            
            newsletter_html += f"""
            <div style="background: #ffffff; border-radius: 4px 4px 0px 0px; border: 1px solid #e6e6e6; padding: 30px; display: flex; flex-direction: column; gap: 10px; align-items: flex-start; justify-content: flex-start; position: relative; flex-shrink: 0;">
                <div style="display: flex; flex-direction: column; gap: 10px; align-items: flex-start; justify-content: flex-start; align-self: stretch; position: relative; flex-shrink: 0;">
                    <div style="border-bottom: 1px solid #8c8c8c; padding: 10px 0px; display: flex; flex-direction: row; align-items: center; justify-content: flex-start; align-self: stretch;">
                        <div style="color: #292929; text-align: left; font-family: 'Arial'; font-size: 18px; font-weight: 700; text-transform: uppercase; position: relative;">{group['topic']}</div>
                    </div>
                </div>
            """
            
            # 키워드를 or로 묶어서 검색
            keywords_combined = 'intext:' + ' OR '.join(group["keywords"])
            news_list = self.get_news(keywords_combined)
            
            # 수집된 뉴스와 검색 키워드를 함께 저장
            for news in news_list:
                news['search_keyword'] = keywords_combined
                all_news.append(news)
                
            grouped_articles = self.group_articles_with_similarity(news_list)
            
            if not news_list:
                newsletter_html += f"<p>오늘은 '{group['topic']}' 관련 뉴스가 없습니다.</p></div>"
                continue
            
            for idx, group in enumerate(grouped_articles):
                for article_idx, article in enumerate(group):
                    if article_idx == 0:
                        article['summary'] = self.summarize_content(article['content'])
                        newsletter_html += f"""
                            <div style="display: flex; flex-direction: row; gap: 0px; padding: 20px 0px 10px 0px; align-items: flex-start; justify-content: flex-start; align-self: stretch; position: relative;">
                                <div style="display: flex; flex-direction: column; gap: 10px; align-items: flex-start; justify-content: flex-start; flex: 1; position: relative;">
                                    <div style="display: flex; flex-direction: column; gap: 15px; align-items: flex-start; justify-content: flex-start; flex: 1; padding-right: 10px;">
                                        <a href="{article['original_url']}" style="color: #292929; text-align: left; font-family: 'Arial'; font-size: 18px; line-height: 130%; font-weight: 700; text-decoration: none;">
                                            {article['title']}
                                        </a>
                                        <div style="color: #292929; text-align: left; font-family: 'Arial'; font-size: 13px; line-height: 140%; font-weight: 400;">
                                            {article['summary']}
                                        </div>
                                    </div>
                                    <div style="padding-right: 10px;">
                                        <div style="color: #292929; text-align: left; font-family: 'Arial'; font-size: 13px; font-weight: 700;">
                                            {article['date']}
                                        </div>
                                    </div>
                                </div>
                                <div style="background: url({article['image_url']}) center; background-size: cover; background-repeat: no-repeat; width: 140px; height: 105px; position: relative; overflow: hidden;">
                                </div>
                            </div>
                        """
                    else:
                        newsletter_html += f"""
                            <a href="{article['original_url']}" style="color: #292929; text-align: left; font-family: 'Arial'; font-size: 13px; font-weight: 700; text-decoration: none;"> ↪ {article['title']}</a>
                        """
            newsletter_html += "</div>"

        # 푸터 추가
        newsletter_html += """
            <div style="background: #000000; border: 1px solid #e6e6e6; padding: 16px 30px 30px 30px; display: flex; flex-direction: column; gap: 25px; align-items: flex-start; justify-content: flex-start; position: relative;">
                <div style="border-bottom: 1px solid #fafafa; padding: 10px 0px; display: flex; flex-direction: row; align-items: center; justify-content: flex-start; align-self: stretch;">
                    <div style="color: #fafafa; text-align: left; font-family: 'Arial'; font-size: 18px; font-weight: 700; text-transform: uppercase;">KETEP INFO</div>
                </div>
                <div style="display: flex; flex-direction: row; align-items: flex-start; justify-content: flex-start; align-self: stretch;">
                    <div style="display: flex; flex-direction: column; gap: 5px; align-items: flex-start; justify-content: flex-start; flex: 1;">
                        <div style="color: #fafafa; text-align: left; font-family: 'Arial'; font-size: 18px; line-height: 130%; font-weight: 700;">
                            Korea Institute of Energy Technology Evaluation and Planning
                        </div>
                        <div style="color: #fafafa; text-align: left; font-family: 'Arial'; font-size: 13px; line-height: 140%; font-weight: 400;">
                            06175 14, Teheran-ro 114-gil, Gangnam-gu, Seoul, Republic of Korea<br />
                            Tel : +82 2-3469-8400 Fax : +82 2-555-2430<br />
                            Copyrightⓒ KETEP. All rights reserved.
                        </div>
                    </div>
                </div>
            </div>
            </div>
        </body>
        </html>
        """
        
        # 모든 뉴스 수집이 완료된 후 CSV 저장
        if all_news:
            self.save_to_csv(all_news)
            
        return newsletter_html

    def send_email(self, html_content, recipients):
        sender_email = os.getenv("SENDER_EMAIL")
        sender_password = os.getenv("SENDER_PASSWORD")
        
        # 이메일 메시지 설정
        msg = MIMEMultipart()
        msg['From'] = sender_email
        today = datetime.now().strftime("%m월 %d일")  # 현재 날짜를 "1월 17일" 형식으로 포맷
        msg['Subject'] = f"{today} KETEP 뉴스레터"  # 메일 제목 수정
        msg.attach(MIMEText(html_content, 'html'))

        try:
            # SMTP 서버 설정
            with smtplib.SMTP_SSL('smtp.naver.com', 465) as server:
                server.login(sender_email, sender_password)  # 비밀번호 입력
                for recipient in recipients:
                    msg['To'] = recipient
                    server.sendmail(sender_email, recipient, msg.as_string())
                    print(f"메일 전송 완료: {recipient}")
        except Exception as e:
            print(f"메일 전송 실패: {str(e)}")

    def schedule_email(self, html_content, recipients, send_time):
        schedule.every().day.at(send_time).do(self.send_email, html_content, recipients)

        while True:
            schedule.run_pending()
            time.sleep(1)

    def generate_newsletter(self):
        newsletter_html = self.generate_html()
        # HTML 파일 저장
        self.save_html(newsletter_html)
        recipients = ["kimyh@ketep.re.kr"]  # 수신자 목록
        self.send_email(newsletter_html, recipients)  # 이메일 전송
        return newsletter_html

    def save_html(self, html_content):
        try:
            # 현재 날짜를 파일명에 포함
            current_dir = os.getcwd()
            # today = datetime.now().strftime("%Y%m%d")
            file_path = os.path.join(current_dir, 'newsletter.html')
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(html_content)
            return file_path
        except Exception as e:
            print(f"HTML 파일 저장 중 오류 발생: {str(e)}")
            return None

def main():
    newsletter_gen = NewsletterGenerator()
    
    print("뉴스레터 생성 중...")
    newsletter_html = newsletter_gen.generate_newsletter()
    
    if newsletter_html:
        print("뉴스레터가 성공적으로 생성되었습니다!")

if __name__ == "__main__":
    main()
