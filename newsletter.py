import os
from dotenv import load_dotenv
import trafilatura
from gnews import GNews
from langchain_openai import ChatOpenAI
from googlenewsdecoder import new_decoderv1
from langchain_core.output_parsers import StrOutputParser
from langchain.prompts import PromptTemplate
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from kiwipiepy import Kiwi
from newspaper import Article
from datetime import datetime, timedelta
import requests
import locale
import ssl
import sqlite3
import json

class NewsletterGenerator:
    def __init__(self):
        load_dotenv()
        locale.setlocale(locale.LC_TIME, 'ko_KR.UTF-8')
        self.keyword_groups = [
            {
                "topic": "에기평",
                "keywords": ["에기평 OR 에너지기술평가원 OR 원장이승재 OR KETEP"],
                "count": 10
            },
            {
                "topic": "산업부",
                "keywords": ["(산업부 OR 산업통상자원부 OR 산자부) (에너지)"],
                "count": 10
            },
            {
                "topic": "원자력",
                "keywords": ["원자력 OR 원자로 OR 원전 OR 방폐물 OR SMR OR 핵융합 OR 핵연료"],
                "count": 10
            },
            { 
                "topic": "수소, 연료전지",
                "keywords": ["수소 OR 연료전지 OR 수전해 OR 개질"],
                "count": 10
            },
            {
                "topic": "태양광",
                "keywords": ["태양광 OR 결정질실리콘 OR 무기박막 OR 유기박막 OR 탠덤태양전지 OR 페로브스카이트"],
                "count": 10
            },
            {
                "topic": "풍력",
                "keywords": ["풍력 OR 해상변전소"],
                "count": 10
            },
            {
                "topic": "전력",
                "keywords": ["전력 (기기 OR 계통 OR 시장 OR 기자재) OR 화력발전 OR 터빈 OR 혼소 OR 송배전 OR 그리드"],
                "count": 10
            },
            {
                "topic": "ESS",
                "keywords": ["에너지저장 OR ESS OR 열저장 OR 배터리 OR 압축공기"],
                "count": 10
            },
            {
                "topic": "에너지수요관리",
                "keywords": ["히트펌프 OR 전동기 OR 유체기기 OR 전력변환 OR VPP OR 에너지효율 OR 수요자원 OR 수요반응"],
                "count": 10
            },     
            {
                "topic": "자원, CCUS",
                "keywords": ["탄소 (포집 OR 저장) OR 온실가스 OR 자원순환 OR CCS OR CCU OR 지중저장 OR 재자원화 OR (천연가스 OR 유가스 OR 핵심광물) (개발 OR 운송)"],
                "count": 10
            },      
            {
                "topic": "에너지안전",
                "keywords": ["(에너지 OR 가스 OR 전기 OR ESS) 안전 OR 안전성평가"],
                "count": 10
            },
            {
                "topic": "기술사업화",
                "keywords": ["기후테크 OR 에너지 (벤처 OR 스타트업 OR 사업화)"],
                "count": 10
            }
        ]
        
        # 검색 기간 설정
        self.period = "일단위"  # "일단위", "주단위", "월단위" 중 선택
        self.start_date = None
        self.end_date = None
        
    def save_to_db(self, news_list):
        try:
            conn = sqlite3.connect('news.db')
            cursor = conn.cursor()
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS news (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic TEXT,
                    keywords TEXT,
                    title TEXT,
                    press TEXT,
                    date TEXT,
                    original_url TEXT UNIQUE,
                    content TEXT
                )
            ''')
            
            saved_count = 0
            duplicate_count = 0
            new_articles = []   # ⭐ 신규 기사 저장
            
            for news in news_list:
                cursor.execute('''
                    INSERT OR IGNORE INTO news 
                    (topic, keywords, title, press, date, original_url, content)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    news['topic'], 
                    news['search_keyword'], 
                    news['title'], 
                    news['press'], 
                    news['date'], 
                    news['original_url'],
                    news['content']
                ))
                
                if cursor.rowcount == 1:   # 새로 저장된 경우만
                    saved_count += 1
                    new_articles.append(news)
                else:
                    duplicate_count += 1
            
            conn.commit()
            conn.close()
            
            print(f"새로 저장된 기사: {saved_count}개")
            print(f"중복된 기사: {duplicate_count}개")
            
            if new_articles:
                self.append_to_current(new_articles)
    
            return 'news.db'
        except Exception as e:
            print(f"DB 저장 중 오류 발생: {str(e)}")
            return None

    
    def append_to_current(self, new_articles):
        current_path = "data/current.json"
        os.makedirs(os.path.dirname(current_path), exist_ok=True)
    
        # 기존 current.json 불러오기
        if os.path.exists(current_path):
            with open(current_path, "r", encoding="utf-8") as f:
                try:
                    existing_data = json.load(f)
                except json.JSONDecodeError:
                    existing_data = []
        else:
            existing_data = []
    
        # ⭐ new_articles(DB 스키마) → JSON 스키마 변환
        normalized_articles = [{
            "topic": n["topic"],
            "keywords": n["search_keyword"],   # ✅ JSON 스키마에 맞춤
            "title": n["title"],
            "press": n["press"],
            "date": n["date"],
            "original_url": n["original_url"],
            "content": n["content"]
        } for n in new_articles]
    
        # 기존 데이터 + 신규 기사 합치기
        updated_data = existing_data + normalized_articles
    
        # 다시 저장
        with open(current_path, "w", encoding="utf-8") as f:
            json.dump(updated_data, f, ensure_ascii=False, indent=4)
    
        print(f"✅ current.json에 {len(normalized_articles)}개 기사 append 완료")

    def export_to_json(self):
        try:
            conn = sqlite3.connect('news.db')
            cursor = conn.cursor()
            
            # 최신 기사부터 가져오도록 정렬
            cursor.execute("""
                SELECT topic, keywords, title, press, date, original_url, content 
                FROM news 
                ORDER BY date DESC, id DESC
            """)
            rows = cursor.fetchall()
            
            # 데이터 리스트 생성
            data = [{
                "topic": row[0], 
                "keywords": row[1], 
                "title": row[2], 
                "press": row[3], 
                "date": row[4], 
                "original_url": row[5],
                "content": row[6]
            } for row in rows]
            
            # JSON 파일로 저장
            with open("news.json", "w", encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            
            conn.close()
            return "news.json"
        except Exception as e:
            print(f"JSON 파일 저장 중 오류 발생: {str(e)}")
            return None

    def get_news(self, keyword):
        # 이미 수집된 기사 URL을 저장할 집합
        if not hasattr(self, 'all_collected_urls'):
            self.all_collected_urls = set()
        
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
                    # Skip if original URL already collected
                    if original_url in self.all_collected_urls:
                        continue
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
                    
                    self.all_collected_urls.add(original_url)
                    
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
    
    # def get_weather_info(self):
    #     try:
    #         today = datetime.now()
    #         today_kst = today + timedelta(hours=9)
    #         date_str = today_kst.strftime('%Y%m%d')
    #         # WeatherAPI.com API 호출
    #         # url1 = f"http://api.weatherapi.com/v1/forecast.json?key=0e50741b3c7142e9b2773529250101&q=Seoul&days=1&aqi=no"
    #         # response1 = requests.get(url1)
    #         # data1 = response1.json()

    #         # 기상청 API 호출
    #         url2 = f"http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst?serviceKey=FD9ka0vGVDdt0SsGDRnaLrR3HgNK5TWkLXgxL5IQ1dmSmLhhDaBCRKgKXQLr%2Bd3iZNkcXAm56M82H3sxldhx5g%3D%3D&numOfRows=1000&pageNo=1&dataType=JSON&base_date={date_str}&base_time=0200&nx=61&ny=125"
    #         response2 = requests.get(url2)
    #         data2 = response2.json()

    #         # WeatherAPI.com에서 날씨 아이콘 가져오기
    #         # icon_url = None
    #         # if response1.status_code == 200:
    #         #     condition = data1['forecast']['forecastday'][0]['day']['condition']
    #         #     icon_url = f"https:{condition['icon']}"  # WeatherAPI.com은 이미 완전한 URL을 제공

    #         # 기상청 API에서 온도 정보 추출
    #         temp_min = None
    #         temp_max = None
    #         if response2.status_code == 200 and data2['response']['header']['resultCode'] == '00':
    #             items = data2['response']['body']['items']['item']

    #             for item in items:
    #                 if item['fcstDate'] == date_str:  # fcstDate가 오늘 날짜인지 확인
    #                     if item['category'] == 'TMX':  # 최고 기온
    #                         temp_max = round(float(item['fcstValue']))
    #                     elif item['category'] == 'TMN':  # 최저 기온
    #                         temp_min = round(float(item['fcstValue']))

    #         return {
    #             'temp_min': temp_min,
    #             'temp_max': temp_max,
    #             # 'icon_url': icon_url  # WeatherAPI.com에서 가져온 아이콘 URL
    #         }
    #     except Exception as e:
    #         print(f"날씨 정보 가져오기 실패: {str(e)}")
    #         return None
        
    def generate_html(self):
        today = datetime.now()  # 현재 UTC 시간
        today_kst = today + timedelta(hours=9)  # 한국 시간으로 변환
        date_str = today_kst.strftime("%Y년 %m월 %d일(%a)")
        all_news = []
        
        # weather_info = self.get_weather_info()
        
        newsletter_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
        </head>
        <body>
            <div style="width: 850px; margin: 0 auto;"><span id="labell_up"></span>
            <div style="background: #ffffff; border-radius: 4px 4px 0px 0px; border: 1px solid #e6e6e6; padding: 30px; display: flex; flex-direction: column; gap: 10px; align-items: flex-start; justify-content: flex-start; position: relative; flex-shrink: 0;">
                <div style="display: flex; flex-direction: column; gap: 10px; align-items: flex-start; justify-content: flex-start; align-self: stretch; position: relative; flex-shrink: 0;">
                    <div style="align-self: stretch; flex-shrink: 0; height: 140px; position: relative; top:60px;">
                        <div style="color: #292929; text-align: left; font-family: 'Arial'; font-size: 57px; letter-spacing: -0.05em; font-weight: 700; position: absolute; right: 4.84%; left: 0%; width: 95.16%; bottom: 0%; top: 0%; height: 100%;">KETEP NEWSBRIEFING</div>
                    </div>
                    <div style="border: 0px; padding: 0px; display: flex; flex-direction: row; align-items: center; justify-content: space-between; align-self: stretch; position: relative; flex-shrink: 0;">
                        <div style="color: #292929; text-align: left; font-family: 'Arial'; font-size: 13px; font-weight: 700; text-transform: uppercase;">{date_str}
                        </div>
                    </div>
                </div>
                <div style="position: absolute; top: 20px; left: 33px; background-color: #292929; border-radius: 3px; width: 32px; height: 32px;">
                    <a href="https://bit.ly/ketepnews" style="display: block; width: 100%; height: 100%; text-decoration: none; color: transparent; font-weight: bold;">NEWS CLOUD</a>
                </div>
                <div style="position: absolute; top: 21px; left: 75px; color: #292929; font-weight: bold; font-size: 10px;line-height: 1.5;">
                    <div>KETEP NEWS CLOUD</div>
                    <div><a href="https://bit.ly/ketepnews" style="text-decoration: none; color: #292929;">https://bit.ly/ketepnews</a></div>
                </div>
            </div>
            <div style="background: #f8f8f8; border: 1px solid #e6e6e6; border-radius: 4px; width: 850px; margin: 0 auto 0 auto; padding: 18px 20px; display: flex; flex-direction: row; justify-content: space-between; align-items: flex-start; gap: 16px; box-sizing: border-box;">
                <!-- 좌측: databus 안내 -->
                <div style="flex: 0 0 230px; min-width: 180px;">
                  <div style="font-size: 15px; font-weight: 700; color: #222; margin-bottom: 15px;">🎓 정부권장교육(AI, 데이터)</div>
                  <a href="https://databus.kr" style="color: #0066cc; font-size: 13px; text-decoration: none;">AI 데이터 역량강화 교육</a>
                </div>
                <!-- 중앙: 무료교육사이트 -->
                <div style="flex: 0 0 230px; min-width: 180px; gap: 6px;">
                    <div style="font-size: 15px; font-weight: 700; color: #222; margin-bottom: 15px;">📚 무료교육(AI, 데이터)</div>
                    <div style="display: flex; flex-direction: column; gap: 6px; font-size: 13px; margin-bottom: 2px;">
                    <a href="https://www.boostcourse.org/opencourse" style="color:#0066cc; text-decoration:none;">네이버 부스트코스</a>
                    <a href="https://alpha-campus.kr/kirdSpecial/list?kirdSpecialClassification1=0a737204-2ae8-45ef-8625-98400b8ac9f5" style="color:#0066cc; text-decoration:none;">과학기술인 알파캠퍼스</a>
                    <a href="https://academy.openai.com/" style="color:#0066cc; text-decoration:none;">OpenAI Academy</a>
                    <a href="https://huggingface.co/learn" style="color:#0066cc; text-decoration:none;">Hugging Face Learn</a>
                    </div>
                </div>
                <!-- 우측: 유튜브 추천 -->
                <div style="flex: 1; display: flex; flex-direction: column; gap: 6px;">
                  <div style="font-size: 15px; font-weight: 700; color: #222; margin-bottom: 8px;">▶️ Youtube(AI)</div>
                  <div style="display: flex; align-items: center; font-size: 13px; margin-bottom: 2px;">
                    <span style="font-weight:600; min-width: 50px; color:#444;">AI 이론</span>
                    <a href="https://www.youtube.com/@3blue1brown" style="color:#0066cc; text-decoration:none; margin-right:6px;">3Blue1Brown</a>
                    <a href="https://www.youtube.com/@code4AI" style="color:#0066cc; text-decoration:none; margin-right:6px;">Discover AI</a>
                    <a href="https://www.youtube.com/@statquest" style="color:#0066cc; text-decoration:none;">StatQuest</a>
                  </div>
                  <div style="display: flex; align-items: center; font-size: 13px; margin-bottom: 2px;">
                    <span style="font-weight:600; min-width: 50px; color:#444;">AI 동향</span>
                    <a href="https://www.youtube.com/@jocoding" style="color:#0066cc; text-decoration:none; margin-right:6px;">조코딩</a>
                    <a href="https://www.youtube.com/@unrealtech" style="color:#0066cc; text-decoration:none; margin-right:6px;">안될공학</a>
                    <a href="https://www.youtube.com/@chester_roh" style="color:#0066cc; text-decoration:none;">노정석</a>
                  </div>
                  <div style="display: flex; align-items: center; font-size: 13px; margin-bottom: 2px;">
                    <span style="font-weight:600; min-width: 50px; color:#444;">AI 활용</span>
                    <a href="https://www.youtube.com/@평범한사업가" style="color:#0066cc; text-decoration:none; margin-right:6px;">평범한사업가</a>
                    <a href="https://www.youtube.com/@oppadu" style="color:#0066cc; text-decoration:none; margin-right:6px;">오빠두엑셀</a>
                    <a href="https://www.youtube.com/@easyworkingai" style="color:#0066cc; text-decoration:none;">일하는 ai</a>
                  </div>
                  <div style="display: flex; align-items: center; font-size: 13px;">
                    <span style="font-weight:600; min-width: 50px; color:#444;">AI 개발</span>
                    <a href="https://www.youtube.com/@aischool_ai" style="color:#0066cc; text-decoration:none; margin-right:6px;">AISchool</a>
                    <a href="https://www.youtube.com/@teddynote" style="color:#0066cc; text-decoration:none; margin-right:6px;">테디노트</a>
                    <a href="https://www.youtube.com/@pyhwpx" style="color:#0066cc; text-decoration:none;">일상의 코딩</a>
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
                news['topic'] = group['topic']
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
        
        # 모든 뉴스 수집이 완료된 후 db 저장
        if all_news:
            self.save_to_db(all_news)
            self.export_to_json()
            
        return newsletter_html

    def generate_newsletter(self):
        newsletter_html = self.generate_html()
        # HTML 파일 저장
        self.save_html(newsletter_html)
        return newsletter_html

    def save_html(self, html_content):
        try:
            # 파일명 고정
            file_path = "newsletter.html"
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
