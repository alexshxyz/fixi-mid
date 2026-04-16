import os
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from parser import parse_and_monitor_match, load_state_from_json
from database import init_db

load_dotenv()


def init_browser(p):
    """Инициализация браузера и страницы"""
    print("Initializing browser...")
    browser = p.chromium.launch(headless=True, args=[
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-infobars",
        "--disable-notifications",
        "--disable-background-networking",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "--disable-extensions",
        "--disable-sync",
        "--metrics-recording-only",
        "--mute-audio",
    ])
    page = browser.new_page()
    page.set_viewport_size({"width": 1280, "height": 720})
    page.goto("https://live5.nowgoal26.com/")
    page.locator("i.closebtn").wait_for(timeout=15000)  # Ждем появления popup для подтверждения загрузки
    print("Page loaded successfully")
    return browser, page


def close_popup(page):
    """Закрытие всплывающего окна"""
    try:
        print("Waiting for popup close button...")
        page.locator("i.closebtn").wait_for(timeout=10000)
        page.locator("i.closebtn").click()
        print("Popup closed")
    except Exception as e:
        print("Popup did not appear or could not be closed")


def switch_to_live(page):
    """Переключение на фильтр Live"""
    try:
        print("Switching to Live...")
        page.locator("li#li_FilterLive").click()
        page.locator("table#table_live").wait_for(timeout=10000)  # Ждем появления таблицы live
        print("Switched to Live")
    except Exception as e:
        print("Failed to switch to Live")


def select_crown(page):
    """Выбор компании Crown и ожидание обновления данных"""
    try:
        print("Selecting company Crown...")
        select = page.locator("select#CompanySel")

        select.select_option(value="3")
        page.wait_for_function(
            "() => { const select = document.querySelector('#CompanySel'); return select && select.value === '3'; }",
            timeout=5000,
        )

        previous_rows = page.evaluate("""
            () => Array.from(document.querySelectorAll('table#table_live tbody tr.tds'))
                .map(row => row.innerText.trim()).join('||')
        """)

        try:
            page.wait_for_function(
                "prev => { const rows = Array.from(document.querySelectorAll('table#table_live tbody tr.tds')); const snapshot = rows.map(row => row.innerText.trim()).join('||'); return snapshot !== prev; }",
                arg=previous_rows,
                timeout=2000,
            )
        except Exception:
            import time
            time.sleep(1)

        print("Crown selected and data updated")
    except Exception as e:
        print("Failed to select Crown")


def configure_odds_settings(page):
    """Настройка отображения odds через settings"""
    try:
        print("Opening settings...")
        page.locator("span#settingBtn").click()
        page.wait_for_selector("input#otc_2", timeout=5000)  # Ждем появления чекбоксов

        page.locator("input#otc_2").set_checked(False)  # Снять чекбокс otc_2
        page.locator("input#otc_3").set_checked(True)   # Включить чекбокс otc_3

        # Закрыть окно настроек
        page.evaluate("MM_showHideLayers('soccerSettingWin','','none');")
        print("Settings configured")
    except Exception as e:
        print("Failed to configure settings")


def collect_matches(page):
    """Сбор списка ID матчей"""
    matches = []
    try:
        print("Counting matches with odds...")
        matches = page.evaluate("""
            () => {
                const matches = [];
                const timeElements = Array.from(document.querySelectorAll('[id^="time_"]'));

                for (const timeElem of timeElements) {
                    if (timeElem.offsetParent === null) continue;
                    const matchId = timeElem.id.replace(/^time_/, '');
                    if (!matchId) continue;

                    const row = timeElem.closest('tr');
                    if (!row) continue;

                    const hasOdds = row.querySelector('p.odds1, p.odds2') !== null;

                    if (hasOdds) {
                        matches.push(matchId);
                    }
                }

                return matches;
            }
        """)
        print(f"Found {len(matches)} matches")
    except Exception as e:
        print("Failed to count matches with odds")
    return matches


def main():
    init_db()
    with sync_playwright() as p:
        browser, page = init_browser(p)
        close_popup(page)
        switch_to_live(page)
        select_crown(page)
        configure_odds_settings(page)
        saved_state = load_state_from_json()
        if saved_state:
            parse_and_monitor_match(page, saved_state=saved_state)
        else:
            matches = collect_matches(page)
            if matches:
                parse_and_monitor_match(page, matches)

        # Оставить браузер открытым для бесконечного мониторинга
        # browser.close()  # Не закрываем, поскольку мониторинг бесконечный


if __name__ == "__main__":
    main()