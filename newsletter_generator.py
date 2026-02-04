import os
import yaml
import json
import glob
from datetime import datetime, timedelta
from typing import Dict, List
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
import requests
import locale
import ssl
import sqlite3
import concurrent.futures
import threading


class NewsletterGenerator:
    """단일 뉴스레터 생성 클래스"""

    def __init__(self, name: str, config: Dict, common_config: Dict):
        self.name = name
        self.config = config
        self.common = common_config
        self.kiwi = Kiwi()  # 한 번만 생성
        self.all_collected_urls = set()
        self.url_lock = threading.Lock()  # 스레드 안전한 URL 집합을 위한 락

        load_dotenv()
        try:
            locale.setlocale(locale.LC_TIME, common_config.get('locale', 'ko_KR.UTF-8'))
        except locale.Error:
            print(f"로케일 설정 실패, 기본 로케일 사용")

    def generate(self):
        """뉴스레터 생성 메인 로직"""
        print(f"\n{'='*60}")
        print(f"뉴스레터 생성: {self.name}")
        print(f"{'='*60}")

        all_news = []

        # 병렬로 토픽별 뉴스 수집
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_to_topic = {
                executor.submit(self.collect_news, topic): topic
                for topic in self.config['topics']
            }

            for future in concurrent.futures.as_completed(future_to_topic):
                topic = future_to_topic[future]
                try:
                    news_list = future.result()
                    print(f"\n토픽 수집: {topic['name']}")

                    # topic 정보 추가
                    for news in news_list:
                        news['topic'] = topic['name']
                        news['search_keyword'] = ' OR '.join(topic['keywords'])

                    all_news.extend(news_list)
                    print(f"수집된 뉴스: {len(news_list)}개")
                except Exception as e:
                    print(f"토픽 '{topic['name']}' 수집 실패: {str(e)}")

        # DB 저장
        if all_news:
            self.save_to_db(all_news)
            self.export_to_json()

            # 월별 JSON 업데이트 (energy 뉴스레터만)
            if self.config.get('monthly_json_enabled'):
                self.update_monthly_json(all_news)

        # HTML 생성
        html = self.generate_html(all_news)
        self.save_html(html)

        print(f"\n뉴스레터 생성 완료: {self.config['output_html']}")

    def collect_news(self, topic: Dict) -> List[Dict]:
        """토픽의 키워드로 뉴스 수집"""
        keywords_combined = 'intext:' + ' OR '.join(topic['keywords'])
        return self.get_news(keywords_combined)

    def _fetch_article_content(self, item: Dict, interval_time: int) -> Dict:
        """개별 뉴스 본문 수집 (병렬 처리용 헬퍼 함수)"""
        try:
            title = item['title']
            source_url = item['url']
            decoded_url = new_decoderv1(source_url, interval=interval_time)
            original_url = decoded_url['decoded_url']

            # 중복 URL 체크 (스레드 안전)
            with self.url_lock:
                if original_url in self.all_collected_urls:
                    return None
                # 본문 수집 전에 미리 추가하여 중복 수집 방지
                temp_added = True
                self.all_collected_urls.add(original_url)

            press = item['publisher']['title']
            date = item['published date']

            # 본문 수집
            downloaded = trafilatura.fetch_url(original_url)
            content = trafilatura.extract(downloaded)

            if not content:
                # 본문이 없으면 URL 제거
                with self.url_lock:
                    self.all_collected_urls.discard(original_url)
                return None

            # 이미지 URL 추출
            main_image = ''
            try:
                original_context = ssl._create_default_https_context
                ssl._create_default_https_context = ssl._create_unverified_context

                article = Article(original_url)
                article.download()
                article.parse()
                main_image = article.top_image

                if main_image and main_image.startswith('http:'):
                    main_image = main_image.replace('http:', 'https:', 1)
            except Exception:
                pass
            finally:
                ssl._create_default_https_context = original_context

            return {
                'title': title,
                'original_url': original_url,
                'press': press,
                'date': date,
                'content': content,
                'summary': '',
                'image_url': main_image
            }

        except Exception:
            return None

    def get_news(self, keyword: str) -> List[Dict]:
        """GNews API로 뉴스 검색 및 수집"""
        period = self.common.get('period', '일단위')

        if period == "일단위":
            when = "1d"
        elif period == "주단위":
            when = "7d"
        elif period == "월단위":
            when = "30d"
        else:
            when = "1d"

        try:
            gnews = GNews(language='ko', country='KR', period=when, max_results=10)
            news_items = gnews.get_news(keyword)
            interval_time = self.common.get('interval_time', 5)

            # 병렬로 뉴스 본문 수집
            news_list = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                future_to_item = {
                    executor.submit(self._fetch_article_content, item, interval_time): item
                    for item in news_items
                }

                for future in concurrent.futures.as_completed(future_to_item):
                    result = future.result()
                    if result:
                        news_list.append(result)

            return news_list
        except Exception as e:
            print(f"뉴스 검색 실패: {str(e)}")
            return []

    def save_to_db(self, news_list: List[Dict]):
        """뉴스 리스트를 SQLite DB에 저장"""
        try:
            db_name = self.config['db_name']
            conn = sqlite3.connect(db_name)
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

                if cursor.rowcount == 1:
                    saved_count += 1
                else:
                    duplicate_count += 1

            conn.commit()
            conn.close()

            print(f"DB 저장 완료: {saved_count}개 신규, {duplicate_count}개 중복")
        except Exception as e:
            print(f"DB 저장 중 오류 발생: {str(e)}")

    def export_to_json(self):
        """DB 내용을 JSON 파일로 내보내기"""
        try:
            db_name = self.config['db_name']
            conn = sqlite3.connect(db_name)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT topic, keywords, title, press, date, original_url, content
                FROM news
                ORDER BY date DESC, id DESC
            """)
            rows = cursor.fetchall()

            data = [{
                "topic": row[0],
                "keywords": row[1],
                "title": row[2],
                "press": row[3],
                "date": row[4],
                "original_url": row[5],
                "content": row[6]
            } for row in rows]

            json_filename = self.config['db_name'].replace('.db', '.json')
            with open(json_filename, "w", encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)

            conn.close()
            print(f"JSON 내보내기 완료: {json_filename} ({len(data)}개 기사)")
        except Exception as e:
            print(f"JSON 파일 저장 중 오류 발생: {str(e)}")

    def update_monthly_json(self, new_articles: List[Dict]):
        """현재 월의 JSON 파일에 신규 기사 추가"""
        # 현재 연-월 계산 (한국 시간 기준)
        today = datetime.now() + timedelta(hours=9)
        year_month = today.strftime("%Y-%m")

        monthly_dir = self.config.get('monthly_json_dir', 'data')
        monthly_path = f"{monthly_dir}/{year_month}.json"

        # 폴더 생성
        os.makedirs(monthly_dir, exist_ok=True)

        # 기존 데이터 로드
        if os.path.exists(monthly_path):
            with open(monthly_path, 'r', encoding='utf-8') as f:
                try:
                    existing_data = json.load(f)
                except json.JSONDecodeError:
                    existing_data = []
        else:
            existing_data = []

        # 중복 제거 (URL 기반)
        existing_urls = {article['original_url'] for article in existing_data}
        new_count = 0

        for article in new_articles:
            if article['original_url'] not in existing_urls:
                existing_data.append({
                    "topic": article['topic'],
                    "keywords": article['search_keyword'],
                    "title": article['title'],
                    "press": article['press'],
                    "date": article['date'],
                    "original_url": article['original_url'],
                    "content": article['content']
                })
                existing_urls.add(article['original_url'])
                new_count += 1

        # 저장
        with open(monthly_path, 'w', encoding='utf-8') as f:
            json.dump(existing_data, f, ensure_ascii=False, indent=4)

        print(f"월별 JSON 업데이트: {monthly_path} (+{new_count}개, 총 {len(existing_data)}개)")

        # index.json 업데이트
        self.update_index_json(monthly_dir)

    def update_index_json(self, data_dir: str):
        """data/index.json 업데이트 (웹사이트용 메타데이터)"""
        index_path = f"{data_dir}/index.json"

        # 모든 월별 JSON 파일 찾기
        monthly_files = sorted(glob.glob(f"{data_dir}/????-??.json"), reverse=True)
        months = [os.path.basename(f).replace('.json', '') for f in monthly_files]

        # 전체 기사 수 계산
        total_count = 0
        for month_file in monthly_files:
            with open(month_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                total_count += len(data)

        # index.json 생성
        index_data = {
            "months": months,
            "total_count": total_count,
            "last_updated": datetime.now().isoformat()
        }

        with open(index_path, 'w', encoding='utf-8') as f:
            json.dump(index_data, f, ensure_ascii=False, indent=2)

        print(f"index.json 업데이트 완료: {len(months)}개월, 총 {total_count}개 기사")

    def analyze_morphology(self, text: str) -> str:
        """형태소 분석 (명사, 동사 추출)"""
        tokens = self.kiwi.analyze(text)
        words = [token[0] for token in tokens[0][0] if token[1] in ('NNG', 'NNP', 'VV')]
        return ' '.join(words)

    def group_articles_with_similarity(self, articles: List[Dict]) -> List[List[Dict]]:
        """유사도 기반 기사 그룹화"""
        valid_articles = [article for article in articles if article.get('content')]
        if not valid_articles:
            return [[article] for article in articles]

        texts = [self.analyze_morphology(article['content']) for article in valid_articles]

        if not texts:
            return [[article] for article in articles]

        vectorizer = TfidfVectorizer(stop_words='english')
        X = vectorizer.fit_transform(texts)

        similarity_matrix = cosine_similarity(X)

        groups = []
        visited = set()

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

        invalid_articles = [article for article in articles if article not in valid_articles]
        for article in invalid_articles:
            groups.append([article])

        return groups

    def summarize_content(self, content: str) -> str:
        """OpenAI API를 사용한 콘텐츠 요약"""
        if not content:
            return "내용이 없습니다."

        try:
            api_key = os.getenv("OPENAI_API_KEY")
            model_name = self.common.get('openai_model', 'gpt-4o-mini')
            prompt = PromptTemplate.from_template("{topic}을 간결하게 3줄로 요약해주세요. 각 문장은 줄바꿈해주세요.")
            model = ChatOpenAI(model=model_name, api_key=api_key)
            chain = prompt | model | StrOutputParser()
            answer = chain.invoke({"topic": content})
            return answer
        except Exception:
            return "요약 생성 실패"

    def generate_html(self, all_news: List[Dict]) -> str:
        """HTML 뉴스레터 생성"""
        today = datetime.now() + timedelta(hours=9)
        date_str = today.strftime("%Y년 %m월 %d일(%a)")

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
        """

        # energy 뉴스레터에만 교육 정보 배너 추가
        if self.name == "energy":
            newsletter_html += """
            <div style="background: #f8f8f8; border: 1px solid #e6e6e6; border-radius: 4px; width: 850px; margin: 0 auto 0 auto; padding: 18px 20px; display: flex; flex-direction: row; justify-content: space-between; align-items: flex-start; gap: 16px; box-sizing: border-box;">
                <div style="flex: 0 0 230px; min-width: 180px;">
                  <div style="font-size: 15px; font-weight: 700; color: #222; margin-bottom: 15px;">🎓 정부권장교육(AI, 데이터)</div>
                  <a href="https://databus.kr" style="color: #0066cc; font-size: 13px; text-decoration: none;">AI 데이터 역량강화 교육</a>
                </div>
                <div style="flex: 0 0 230px; min-width: 180px; gap: 6px;">
                    <div style="font-size: 15px; font-weight: 700; color: #222; margin-bottom: 15px;">📚 무료교육(AI, 데이터)</div>
                    <div style="display: flex; flex-direction: column; gap: 6px; font-size: 13px; margin-bottom: 2px;">
                    <a href="https://www.boostcourse.org/opencourse" style="color:#0066cc; text-decoration:none;">네이버 부스트코스</a>
                    <a href="https://alpha-campus.kr/kirdSpecial/list?kirdSpecialClassification1=0a737204-2ae8-45ef-8625-98400b8ac9f5" style="color:#0066cc; text-decoration:none;">과학기술인 알파캠퍼스</a>
                    <a href="https://academy.openai.com/" style="color:#0066cc; text-decoration:none;">OpenAI Academy</a>
                    <a href="https://huggingface.co/learn" style="color:#0066cc; text-decoration:none;">Hugging Face Learn</a>
                    </div>
                </div>
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

        # 토픽별 뉴스 섹션 생성
        for topic_config in self.config['topics']:
            topic_name = topic_config['name']
            topic_news = [n for n in all_news if n.get('topic') == topic_name]

            newsletter_html += f"""
            <div style="background: #ffffff; border-radius: 4px 4px 0px 0px; border: 1px solid #e6e6e6; padding: 30px; display: flex; flex-direction: column; gap: 10px; align-items: flex-start; justify-content: flex-start; position: relative; flex-shrink: 0;">
                <div style="display: flex; flex-direction: column; gap: 10px; align-items: flex-start; justify-content: flex-start; align-self: stretch; position: relative; flex-shrink: 0;">
                    <div style="border-bottom: 1px solid #8c8c8c; padding: 10px 0px; display: flex; flex-direction: row; align-items: center; justify-content: flex-start; align-self: stretch;">
                        <div style="color: #292929; text-align: left; font-family: 'Arial'; font-size: 18px; font-weight: 700; text-transform: uppercase; position: relative;">{topic_name}</div>
                    </div>
                </div>
            """

            if not topic_news:
                newsletter_html += f"<p>오늘은 '{topic_name}' 관련 뉴스가 없습니다.</p></div>"
                continue

            grouped_articles = self.group_articles_with_similarity(topic_news)

            # 병렬로 요약 생성
            articles_to_summarize = []
            for group in grouped_articles:
                if group:
                    articles_to_summarize.append(group[0])

            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                summary_futures = {
                    executor.submit(self.summarize_content, article['content']): article
                    for article in articles_to_summarize
                }

                summary_map = {}
                for future in concurrent.futures.as_completed(summary_futures):
                    article = summary_futures[future]
                    try:
                        summary_map[id(article)] = future.result()
                    except Exception:
                        summary_map[id(article)] = "요약 생성 실패"

            # HTML 생성 (요약 결과 사용)
            summary_idx = 0
            for group in grouped_articles:
                for article_idx, article in enumerate(group):
                    if article_idx == 0:
                        summary = summary_map.get(id(article), "요약 생성 실패")
                        newsletter_html += f"""
                            <div style="display: flex; flex-direction: row; gap: 0px; padding: 20px 0px 10px 0px; align-items: flex-start; justify-content: flex-start; align-self: stretch; position: relative;">
                                <div style="display: flex; flex-direction: column; gap: 10px; align-items: flex-start; justify-content: flex-start; flex: 1; position: relative;">
                                    <div style="display: flex; flex-direction: column; gap: 15px; align-items: flex-start; justify-content: flex-start; flex: 1; padding-right: 10px;">
                                        <a href="{article['original_url']}" style="color: #292929; text-align: left; font-family: 'Arial'; font-size: 18px; line-height: 130%; font-weight: 700; text-decoration: none;">
                                            {article['title']}
                                        </a>
                                        <div style="color: #292929; text-align: left; font-family: 'Arial'; font-size: 13px; line-height: 140%; font-weight: 400;">
                                            {summary}
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

        # 푸터
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

        return newsletter_html

    def save_html(self, html_content: str):
        """HTML 파일 저장"""
        try:
            file_path = self.config['output_html']
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(html_content)
            print(f"HTML 저장 완료: {file_path}")
        except Exception as e:
            print(f"HTML 파일 저장 중 오류 발생: {str(e)}")


def main():
    """메인 실행 함수"""
    # config.yaml 로드
    config_path = 'config.yaml'
    if not os.path.exists(config_path):
        print(f"오류: {config_path} 파일을 찾을 수 없습니다.")
        return

    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # 각 뉴스레터 생성
    for name, nl_config in config['newsletters'].items():
        generator = NewsletterGenerator(name, nl_config, config['common'])
        generator.generate()

    print("\n" + "="*60)
    print("모든 뉴스레터 생성 완료!")
    print("="*60)


if __name__ == "__main__":
    main()
