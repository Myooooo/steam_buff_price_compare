import os
import sys
import time
import json
import string
import urllib.request
from urllib.parse import urlencode
from urllib.parse import quote

headers = {
        'User-Agent' : 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/85.0.4183.121 Safari/537.36',
        'Cookie' : ''
        }

# get current price from Steam
# appid=730 for CSGO, currency=23 for CNY
def getSteamPrice(market_hash_name,appid,currency):

    url = 'http://steamcommunity.com/market/priceoverview/?'
    parameters = {
                'market_hash_name':market_hash_name,
                'appid':appid,
                'currency':currency
                }

    url = url + urlencode(parameters)

    # send request and parse response
    try:
        response = json.loads(urllib.request.urlopen(url).read())
    except:
        print('\t发送Steam请求失败')
        return -1

    # read price from json
    if response['success'] == True:
        # check if entries exist
        if 'lowest_price' not in response:
            print('\t读取Steam价格失败')
            return -1
        # if 'median_price' in response:
        #     median_price = response['median_price']
        # if 'volume' in response:
        #     volume = response['volume']

        # read lowest price
        lowest_price = response['lowest_price'].replace(',','')

        #print('\t读取Steam价格成功')
        print('\tSteam最低价格:',lowest_price)
        #print('Steam中位价格:',median_price)
        #print('Steam市场存量:',volume)
        
        # parse variables
        lowest_price = float(lowest_price[2:len(lowest_price)])
        #median_price = float(median_price[2:len(median_price)])
        #volume = int(volume)

        return lowest_price
    else:
        print('\t读取Steam价格失败')
        return -1

# calculate discount rate percentage
def processItem(item):
    steam_lowest_price = getSteamPrice(item['market_hash_name'],'730','23')
    buff_lowest_price = float(item['sell_min_price'])

    # calculate price difference
    if steam_lowest_price != -1:
        received = steam_lowest_price / 1.15
        per_diff = ( buff_lowest_price / received ) * 100
        return received,per_diff
    
    return 0,100

# search keyword on Buff
def searchBuff(keyword):
    url = 'https://buff.163.com/api/market/goods?game=csgo&page_num=1&search=' + keyword
    url = quote(url,safe=string.printable)

    # send request and read response
    try:
        response = urllib.request.Request(url,headers=headers)
        response = urllib.request.urlopen(response).read()
    except:
        print('\n发送Buff请求失败,请检查网络')
        return -1

    # write cache
    file_path = os.path.join(os.path.dirname(__file__),'./cache/')
    file_name = keyword + ".json"
    
    try:
        if not os.path.exists(os.path.realpath(file_path)):
            os.mkdir(os.path.realpath(file_path))
        with open(os.path.realpath(file_path + file_name), "wb") as fw:
            fw.write(response)
    except IOError:
        print('\n写入缓存失败')

    # parse response
    response_json = json.loads(response)
    
    if response_json['code'] == 'OK':
        print('\n搜索Buff成功')
        pass
    elif response_json['code'] == 'Login Required':
        print('\n搜索Buff失败 请重新登录并更新Cookie')
        return -2
    else:
        print('\n搜索Buff失败 发生未知错误')
        return -3
    
    result_count = len(response_json['data']['items'])
    print('找到%d个结果\n' %result_count)

    
    # process items
    for i in range(result_count):
        item = response_json['data']['items'][i]
        print('\n[%d] %s\n' %((i+1),item['market_hash_name']))
        received,per_diff = processItem(item)

        print('\tBuff最低价格: ¥',item['sell_min_price'])
        #print('市场存量:',item['sell_num'])
        #print('最高求购价格: ¥',item['buy_max_price'])
        #print('求购数量:',item['buy_num'])
        print('\t到手余额: ¥%.2f' %received)
        print('\t差价百分比: %.2f%%' %per_diff)

        # pause to avoid ban
        time.sleep(0.5)
    
    print('\n搜索完成\n')
    return 0
    
if __name__ == '__main__':
    keyword = input('搜索饰品：')
    while keyword:
        rtn = searchBuff(keyword.replace(" ", "+"))
        keyword = input('搜索饰品：')