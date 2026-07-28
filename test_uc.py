import undetected_chromedriver as uc
import time

if __name__ == '__main__':
    print("Starting Chrome...")
    try:
        options = uc.ChromeOptions()
        driver = uc.Chrome(options=options)
        print("Chrome started!")
        driver.get("https://www.mombasacomputers.com/")
        print("Got page. Title:", driver.title)
        time.sleep(10)
        driver.quit()
    except Exception as e:
        import traceback
        traceback.print_exc()
