import argparse
import traceback
from datetime import datetime, timedelta
import os

import pendulum
import requests
from notion_helper import NotionHelper

from weread_api import WeReadApi
import utils
from config import book_properties_type_dict, tz
from retrying import retry
import json
from datetime import datetime

TAG_ICON_URL = "https://www.notion.so/icons/tag_gray.svg"
USER_ICON_URL = "https://www.notion.so/icons/user-circle-filled_gray.svg"
BOOK_ICON_URL = "https://www.notion.so/icons/book_gray.svg"

rating = {"poor": "⭐️", "fair": "⭐️⭐️⭐️", "good": "⭐️⭐️⭐️⭐️⭐️"}


@retry(stop_max_attempt_number=3, wait_fixed=5000)
def get_douban_url(isbn):
    print(f"get_douban_url {isbn} ")
    params = {"query": isbn, "page": "1", "category": "book"}
    r = requests.get("https://neodb.social/api/catalog/search", params=params)
    books = r.json().get("data")
    if books is None or len(books) == 0:
        return None
    results = list(filter(lambda x: x.get("isbn") == isbn, books))
    if len(results) == 0:
        return None
    result = results[0]
    urls = list(
        filter(
            lambda x: x.get("url").startswith("https://book.douban.com"),
            result.get("external_resources", []),
        )
    )
    if len(urls) == 0:
        return None
    return urls[0].get("url")


def insert_book_to_notion(books, index, bookId):
    """插入Book到Notion"""
    book = {}
    if bookId in archive_dict:
        book["书架分类"] = archive_dict.get(bookId)
    if bookId in notion_books:
        book.update(notion_books.get(bookId))

    bookInfo = weread_api.get_bookinfo(bookId)
    if bookInfo != None:
        book.update(bookInfo)
    readInfo = weread_api.get_read_info(bookId)
    # 研究了下这个状态不知道什么情况有的虽然读了状态还是1 markedStatus = 1 想读 4 读完 其他为在读
    readInfo.update(readInfo.get("readDetail", {}))
    readInfo.update(readInfo.get("bookInfo", {}))
    book.update(readInfo)
    book["阅读进度"] = (
        100 if (book.get("markedStatus") == 4) else book.get("readingProgress", 0)
    ) / 100
    markedStatus = book.get("markedStatus")
    status = "想读"
    if markedStatus == 4:
        status = "阅读完"
    elif book.get("readingTime", 0) >= 60:
        status = "在读"
    book["阅读状态"] = status
    book["阅读时长"] = book.get("readingTime")
    book["阅读天数"] = book.get("totalReadDay")
    #book["评分"] = book.get("newRating")
    if book.get("newRatingDetail") and book.get("newRatingDetail").get("myRating"):
        book["我的评分"] = rating.get(book.get("newRatingDetail").get("myRating"))
    elif status == "已读":
        book["我的评分"] = "未评分"
    date = None
    if book.get("finishedDate"):
        date = book.get("finishedDate")
    elif book.get("lastReadingDate"):
        date = book.get("lastReadingDate")
    elif book.get("readingBookDate"):
        date = book.get("readingBookDate")
    book["时间"] = date
    book["类型"] = "书籍"
    #book["开始阅读时间"] = book.get("beginReadingDate")
    #book["最后阅读时间"] = book.get("lastReadingDate")
    cover = book.get("cover").replace("/s_", "/t7_")
    if not cover and not cover.strip() and not cover.startswith("http"):
        cover = BOOK_ICON_URL
    if bookId not in notion_books:
        isbn = book.get("isbn")
        #链接timeout，先注释
        #if isbn and isbn.strip():
        #    douban_url = get_douban_url(isbn)
        #    if douban_url:
        #        book["douban_url"] = douban_url
        book["标题"] = book.get("title")
        book["BookId"] = book.get("bookId")
        book["ISBN"] = book.get("isbn")
        book["链接"] = utils.get_weread_url(bookId)
        #book["简介"] = book.get("intro")
        #book["作者"] = [
        #    notion_helper.get_relation_id(
        #        x, notion_helper.author_database_id, USER_ICON_URL
        #    )
        #    for x in book.get("author").split(" ")
        #]
        #if book.get("categories"):
        #    book["分类"] = [
        #        notion_helper.get_relation_id(
        #            x.get("title"), notion_helper.category_database_id, TAG_ICON_URL
        #        )
        #        for x in book.get("categories")
        #    ]
    properties = utils.get_properties(book, book_properties_type_dict)

    #print(f"正在插入《{book.get('title')}》,一共{len(books)}本，当前是第{index+1}本。")
    #if not book.get("readDetail") or not book.get("readDetail").get("data"):
    #    print(f"《{book.get('title')}》没有阅读记录，跳过")
    #    return

    if book.get("readDetail") and book.get("readDetail").get("data"):
        #取书籍的阅读记录，可能有多天
        book['时间'] = [x['readDate'] for x in book.get('readDetail').get('data')]
        notion_helper.get_date_relations(
            properties,
            #时间戳转换为北京时间
            [pendulum.from_timestamp(x, tz="Asia/Shanghai") for x in book.get("时间")]
        )

    parent = {"database_id": notion_helper.book_database_id, "type": "database_id"}
    result = None
    if bookId in notion_books:
        result = notion_helper.update_page(
            page_id=notion_books.get(bookId).get("pageId"),
            properties=properties,
            cover=utils.get_icon(cover),
        )
    else:
        result = notion_helper.create_book_page(
            parent=parent,
            properties=properties,
            icon=utils.get_icon(cover),
        )
    page_id = result.get("id")
    if book.get("readDetail") and book.get("readDetail").get("data"):
        data = book.get("readDetail").get("data")
        data = {item.get("readDate"): item.get("readTime") for item in data}
        #不用同步阅读记录了
        #insert_read_data(page_id, data)


def insert_read_data(page_id, readTimes):
    readTimes = dict(sorted(readTimes.items()))
    filter = {"property": "书架", "relation": {"contains": page_id}}
    #拿到当前书籍$page_id的所有阅读记录
    results = notion_helper.query_all_by_book(notion_helper.read_database_id, filter)
    for result in results:
        timestamp = result.get("properties").get("时间戳").get("number")
        duration = result.get("properties").get("时长").get("number")
        id = result.get("id")
        if timestamp in readTimes:
            value = readTimes.pop(timestamp)
            if value != duration:
                insert_to_notion(
                    page_id=id,
                    timestamp=timestamp,
                    duration=value,
                    book_database_id=page_id,
                )
    for key, value in readTimes.items():
        insert_to_notion(None, int(key), value, page_id)


#插入或更新阅读记录
def insert_to_notion(page_id, timestamp, duration, book_database_id):
    parent = {"database_id": notion_helper.read_database_id, "type": "database_id"}
    properties = {
        "标题": utils.get_title(
            pendulum.from_timestamp(timestamp, tz=tz).to_date_string()
        ),
        "日期": utils.get_date(
            start=pendulum.from_timestamp(timestamp, tz=tz).format(
                "YYYY-MM-DD HH:mm:ss"
            )
        ),
        "时长": utils.get_number(duration),
        "时间戳": utils.get_number(timestamp),
        "书架": utils.get_relation([book_database_id]),
    }
    if page_id != None:
        notion_helper.client.pages.update(page_id=page_id, properties=properties)
    else:
        notion_helper.client.pages.create(
            parent=parent,
            icon=utils.get_icon("https://www.notion.so/icons/target_red.svg"),
            properties=properties,
        )


if __name__ == "__main__":
    current_time = datetime.now()
    print("开始同步阅读记录，当前时间: ", current_time)
    weread_api = WeReadApi()
    notion_helper = NotionHelper()
    notion_books = notion_helper.get_all_book()
    print("notion已有书籍...")
    print(notion_books)
    bookshelf_books = weread_api.get_bookshelf()
    print("微信书架上的所有数据...")
    print(bookshelf_books)
    # 有阅读记录的图书信息
    # 样例数据：{'$bookId': {'bookId': '26062915', 'progress': 1, 'chapterUid': 8, 'chapterOffset': 0,
    #   'chapterIdx': 8, 'appId'： '11413501'， 'updateTime': 1691221509, 'readingTime': 875,
    #   'syncKey': 720606794}
    # }
    bookProgress = bookshelf_books.get("bookProgress")
    bookProgress = {book.get("bookId"): book for book in bookProgress}
    archive_dict = {}
    #acchive是书单的名字
    for archive in bookshelf_books.get("archive"):
        name = archive.get("name")
        bookIds = archive.get("bookIds")
        archive_dict.update({bookId: name for bookId in bookIds})
    not_need_sync = []
    # Notion里的文献笔记
    # 样例数据：{'$bookId': {'pageId': '10c911dc-da56-8106-8b58-ddaacc287c74', 'readingTime': 2325,
    # 'category': None, 'Sort': None, 'douban_url': None,
    # 'cover': {'type': 'external', 'external': {'url': 'https://bts-image.xyzcdn.net/aHR0cHM6Ly9pbWFnZS54eXpjZG4ubmV0L0ZxUWs2VThtWDU0YnZ3MFBsbm5HemtHMVpEajkuanBn.jpg'}},
    # 'myRating': None, 'comment': None, 'status': '阅读完'}
    # }
    for key, value in notion_books.items():
        if (
            (
                key not in bookProgress
                or value.get("readingTime") == bookProgress.get(key).get("readingTime")
            )
            and (archive_dict.get(key) == value.get("category"))
            #and (value.get("cover") is not None)
            #and (
            #    value.get("status") != "已读"
            #    or (value.get("status") == "已读" and value.get("myRating"))
            #)
        ):
            #这里判定Notion中的书籍和微信中的书籍是否有属性发现变化，没有的话就不需要同步了
            not_need_sync.append(key)
            #continue
    print("不需要同步的书籍")
    print(not_need_sync)
    notebooks = weread_api.get_notebooklist()
    print("微信notebook的所有书籍...")
    print(notebooks)
    notebooks_map = {d["bookId"] : d for d in notebooks if "bookId" in d}
    notebooks = [d["bookId"] for d in notebooks if "bookId" in d]

    books = bookshelf_books.get("books")
    bookshelf_map = {d["bookId"] : d for d in books if "bookId" in d}
    books = [d["bookId"] for d in books if "bookId" in d]


    books = list((set(notebooks) | set(books)) - set(not_need_sync))
    len = len(books)
    for index, bookId in enumerate(books):
        try:
            bp = bookProgress[bookId] if bookId in bookProgress  else None
            bs = bookshelf_map[bookId] if bookId in bookshelf_map else None
            nt = notebooks_map[bookId] if bookId in notebooks_map else None

            title = bs['title'] if bs and 'title' in bs else ""
            print(f"正在插入《{title}》,一共{len}本，当前是第{index + 1}本。")

            if bp and bp['readingTime'] < 60:
                print(f"《{title}》阅读不超时1分钟，跳过")
                continue

            #if not nt:
            #    print(f"《{title}》没有阅读笔记，跳过")
            #    continue

            insert_book_to_notion(books, index, bookId)
        except Exception as e:
            print("处理book: " + bookId + "出现异常，跳过:")
            traceback.print_exc()
            continue
