
import os, sys, re
from glob import glob
from datetime import date, datetime
from shutil import rmtree
from cdecimal import Decimal
import googlemaps

import requests
from bs4 import BeautifulSoup

# Must have chromedriver.exe in basepath
from selenium import webdriver
from selenium.webdriver.support.ui import Select

import pandas as pd
import numpy as np

# Supply info.py with:
# - Google Gecoding API Key
# - Username and Password for Events and Adventures
# - Home Addresss
# - Work Address
from info import (GOOGLE_MAPS_KEY, EA_USERNAME,
                  EA_PASSWORD, HOME, WORK)


def login():
    # Login to Events and Adventures
    login_url = 'https://singles.eventsandadventures.com/website/logon.aspx'
    driver = webdriver.Chrome('./chromedriver')
    driver.get(login_url)
    driver.find_element_by_id('contentMain_username').send_keys(EA_USERNAME)
    driver.find_element_by_id('contentMain_password').send_keys(EA_PASSWORD)
    driver.find_element_by_id('contentMain_btnSubmit').click()
    return driver


class EALoader(object):

    def __init__(self):
        # Constant Variables
        self.__two_decimals = Decimal('0.01')
        self.__special_columns = ['event_status', 'member_status', 'sitename', 'city', 'state', 'event_day', 'dist_from_home', 
                                 'dist_from_work', 'time_from_work', 'time_from_home',]

        self._output_fields = ['attending', 'sign_up', 'wait_list', 'cancel', 'event_status', 'member_status', 'event_name',
                               'event_location', 'event_day', 'event_date', 'signup_before', 'cancel_before', 'event_cost',
                               'event_tax', 'venue_cost', 'spots_left', 'attendees', 'limit', 'duration', 'dist_from_home',
                               'time_from_home', 'dist_from_work', 'time_from_work', 'street', 'city', 'state', 'code',
                               'phone', 'address', 'host', 'attire', 'sitename', 'url',]

        # Google Map API
        self._map = googlemaps.Client(GOOGLE_MAPS_KEY)
        
        print('\nGathering Event Data...')
        self._data = self._get_event_data()

        print('\nProducing DataFrame...')
        self.df = self._produce_dataframe()

        self.write_files()


    def _get_event_links(self, driver):
        return [i.get_attribute('href') for i in driver.find_elements_by_class_name('calevent')]


    def _extract_event_data(self, driver, link):
        driver.get(link)
        find = driver.find_element_by_id

        attending, sign_up, wait_list, cancel = None, None, None, None

        event_name, event_location = find('contentMain_eventnamelocation').text.encode('utf-8').split('\n')
        if 'new member' in event_name.lower() or 'host meeting' in event_name.lower():
            return None

        if 'no event' in event_location.lower():
            return None

        event_day, event_date = self._parse_date(find('contentMain_datetime').text.strip())
        if event_date < datetime.now():
            # print('-> {:>9}: {}'.format('Passed', event_name))
            return None

        event_status = find('contentMain_eventstatus').text.encode('utf-8').strip().lower()
        if event_status == 'event has passed':
            # print('-> {:>9}: {}'.format('Passed', event_name))
            return None
        elif 'full' in event_status:
            event_status = 'full'
        elif 'available' in event_status:
            event_status = 'available'

        member_status = find('contentMain_signupstatus').text.encode('utf-8').strip().lower()
        if 'cancel' in member_status:
            # print('-> {:>9}: {}'.format('Cancelled', event_name))
            return None
        elif 'you are' in member_status:
            member_status = 'signed up'
            attending = 'x'

        _, signup_before = self._parse_date(find('contentMain_signupbefore').text.strip())
        if signup_before < datetime.now():
            # print('-> {:>9}: {}'.format('Closed', event_name))
            return None

        if 'full' in event_status:
            print '-> {:>9}: {}'.format('Full', event_name)
        elif member_status == 'signed up':
            print '-> {:>9}: {}'.format('Signed Up', event_name)
        else:
            print('-> {:>9}: {}'.format('Available', event_name))

        _, cancel_before = self._parse_date(find('contentMain_cancelbefore').text.strip())
        host = ' - '.join(sorted([i.strip() for i in find('contentMain_hosts').text.encode('utf-8').replace('[Photo]','').strip().split('\n')]))
        # event_type = find('contentMain_eventtype').text.encode('utf-8') or None
        duration = self._parse_duration(find('contentMain_duration').text.encode('utf-8'))
        attire = find('contentMain_attire').text.encode('utf-8').strip().title()
        venue_cost = self._parse_cost(find('contentMain_venuecost').text.encode('utf-8').strip())
        event_cost = self._parse_cost(find('contentMain_eventcost').text.encode('utf-8').strip())
        event_tax = self._parse_cost(find('contentMain_eventtax').text.encode('utf-8').strip())

        event_cost = self._determine_cost(event_name, find('contentMain_eventdescription').text.encode('utf-8').strip(), event_cost)

        address = find('contentMain_venueaddress').text.encode('utf-8').replace('\n', ' ').strip()
        dist_from_home, time_from_home = self._extract_travel_data(HOME, address)
        dist_from_work, time_from_work = self._extract_travel_data(WORK, address)
        street, city, state, code = self._parse_address(address)
        phone = self._parse_phone(address)
        if street or city or state:
            address = None
        else:
            address = address.replace(',', '')

        sitename = find('contentMain_sitename').text.encode('utf-8').strip()

        # Move to signup page
        find('contentMain_lnkSignup').click()
        attendees, limit, spots = self._parse_limit(find('contentMain_memberlimit').text.encode('utf-8').strip())

        return (
            attending, sign_up, wait_list, cancel, event_status, member_status, event_name, event_location,
            event_day, event_date, signup_before, cancel_before, event_cost, event_tax, venue_cost, spots,
            attendees, limit, duration, dist_from_home, time_from_home, dist_from_work, time_from_work,
            street, city, state, code, phone, address, host, attire, sitename, link, 
        )


    def _get_event_data(self):
        driver = login()
        driver.find_element_by_id('PublicNav1_lnkCalendar').click()
        cal_url = driver.current_url

        data = list()
        
        # Parse events for current month
        event_links = []
        for link in self._get_event_links(driver):
            item = self._extract_event_data(driver, link)
            if item:
                data.append(item)

        # Parse events for next month
        driver.get(cal_url)
        year, month = self._produce_date(1)
        select = Select(driver.find_element_by_id('contentMain_lstmonths'))
        select.select_by_value('{}/1/{}'.format(month, year))
        for link in self._get_event_links(driver):
            item = self._extract_event_data(driver, link)
            if item:
                data.append(item)

        # Parse events two months out
        driver.get(cal_url)
        year, month = self._produce_date(2)
        select = Select(driver.find_element_by_id('contentMain_lstmonths'))
        select.select_by_value('{}/1/{}'.format(month, year))
        for link in self._get_event_links(driver):
            item = self._extract_event_data(driver, link)
            if item:
                data.append(item)

        driver.close()
        return data


    def _produce_dataframe(self):
        df = pd.DataFrame(self._data, columns=self._output_fields)
        return df.sort_values(by=['sitename', 'event_status', 'member_status', 'spots_left', 'attendees', 'event_date', 'event_cost'],
                            ascending=[True, True, True, True, False, True, True])


    ########################
    #### Parser Methods ####
    ########################

    def _determine_cost(self, name, description, cost):
        pattern = re.compile(
            r'[$]+(?P<amount>[\d,]*[.]?[\d]?)'
        )
        cost = Decimal(cost)
        match = re.findall(pattern, description)
        if match:
            match_set = set([Decimal(m.replace(',', '')) for m in match])
            if len(match_set) == 1:
                amt = list(match_set)[0]
                if amt == cost:
                    return cost
                else:
                    return max([amt, cost])
            elif len(match_set) > 1:
                if 'volleyball' in name.lower():
                    return min(list(match_set))
                else:
                    return cost
        else:
            return cost
        

    def _parse_phone(self, address):
        pattern = re.compile(
            r'[\w\s,]*[(]?(?P<areacode>[\d]{3})[).-]*[\s]*(?P<three>[\d]{3})[-.]*(?P<four>[\d]{4})'
        )
        try:
            match = re.search(pattern, address).groupdict()
            a,b,c = match['areacode'], match['three'], match['four']
            return '{}-{}-{}'.format(a, b, c)
        except AttributeError:
            return None


    def _parse_limit(self, limit):
        pattern = re.compile(
            r'(?P<attending>[\d]*)[\s]?attending[\s]?[/]?[\s]?(?P<limit>[\d\w]*)[\s]?limit'
        )
        try:
            match = re.match(pattern, limit.lower()).groupdict()
            attendees, limit = match['attending'], match['limit']
            attendees = Decimal(attendees)
            limit = Decimal(limit) if 'no' != limit.lower() else None
            spots_left = None
            if limit:
                spots_left = limit - attendees
            return attendees, limit, spots_left
        except AttributeError:
            return None, None, None


    def _parse_cost(self, cost):
        pattern = re.compile(
            r'[$]?(?P<amount>[\d\,]*[.]+[\d]*)'
        )
        try:
            match = re.match(pattern, cost).groupdict()
            return Decimal(match['amount'].replace(',', ''))
        except AttributeError:
            return None


    def _parse_duration(self, dur):
        pattern = re.compile(
            r'(?P<number>[\d]*[.]?[\d]?)[+]?\s(?P<timeframe>[\w]*)'
        )
        try:
            match = re.match(pattern, dur).groupdict()
            num, tframe = Decimal(match['number']), match['timeframe']
            if 'night' in tframe or 'day' in tframe:
                num *= Decimal('24.0')
            return num
        except AttributeError:
            return None


    def _parse_date(self, dstr):
        weekday_dict = {0: 'Monday', 1: 'Tuesday', 2: 'Wednesday', 3: 'Thursday', 4: 'Friday', 5: 'Saturday', 6: 'Sunday'}
        month_dict = {'January': 1, 'February': 2, 'March': 3, 'April': 4, 'May': 5, 'June': 6, 'July': 7,
                      'August': 8, 'September': 9, 'October': 10, 'November': 11, 'December': 12}
        
        pattern = re.compile(
            r'[\w]*[\s]+(?P<month>[\w]*)[\s]+(?P<day>[\d]{1,2})[,\s]+(?P<year>[\d]{4})[\s]+(?P<hour>[\d]{1,2})[:]+(?P<minute>[\d]{1,2})[\s]+(?P<sign>[\w]{2})'
        )
        try:
            match = re.search(pattern, dstr).groupdict()
            month = month_dict[match['month']]
            day = int(match['day'])
            year = int(match['year'])
            hour = int(match['hour'])
            minute = int(match['minute'])
            sign = match['sign'].lower()

            if sign == 'pm' and hour != 12:
                hour += 12

            result = datetime(year, month, day, hour, minute)
            return weekday_dict[result.weekday()], result
        except AttributeError:
            return None, None


    def _parse_event(self, tag):
        location = tag.find('br').text.strip()
        name = tag.text.replace(location, '', 1).strip()
        return name.strip(), location.strip()


    def _parse_address(self, addr):
        if "We don't publish member addresses. Address emailed to those signed up" in addr:
            addr = addr.replace("We don't publish member addresses. Address emailed to those signed up", '')
        res = self._map.geocode(addr)
        if len(res):
            res = res[0].get('formatted_address', None)
            if res is None:
                return None, None, None, None

            res = [i.strip() for i in res.split(',')]
            street = res[0]
            city = res[1]

            if ' ' in res[2]:
                state, zcode = [i.strip() for i in res[2].split(' ')]
            else:
                state, zcode = res[2], None

            if street[-len(city):] == city.lower():
                street = street.replace(city.lower(), '')

            return street, city, state, zcode
        else:
            return None, None, None, None


    def _extract_travel_data(self, addr1, addr2):
        res = self._map.distance_matrix(addr1, addr2)
        try:
            status = res.get('rows')[0].get('elements')[0].get('status')
            if status.lower() == 'not_found':
                return None, None

            res = res.get('rows')[0].get('elements')[0]
            miles = self._convert_km_to_miles(res.get('distance').get('text').replace(',', ''))
            time = res.get('duration').get('text')
            minutes = 0

            if 'hour' in time:
                hour = int(time.split(' ')[0])
                minutes = hour * 60
                minutes += int(time.split(' ')[2])
            else:
                minutes += int(time.split(' ')[0])

            miles = Decimal(miles)
            minutes = Decimal(minutes)
            return miles, minutes
        except TypeError:
            pass
        except AttributeError:
            pass
        return None, None


    ########################
    #### Helper Methods ####
    ########################

    def _produce_date(self, num):
        today = date.today()
        year, month = today.year, today.month

        month += num
        if month > 12:
            month %= 12
            year += 1
        return year, month


    def _convert_km_to_miles(self, km):
        km = km.split(' ')[0]
        km = Decimal(km)
        return (km * Decimal('0.62137')).quantize(self.__two_decimals)


    def _range_writer(self, df, col, directory):
        nums = [10, 15, 20, 25, 30, 40, 50, 60, 100]
        DF = df.copy()
        file_col = col.replace('_', ' ')
        for idx, num in enumerate(nums):
            outputs = []
            if idx == 0:
                filename = '{} less than {}.csv'.format(file_col, num)
                df = DF.loc[DF[col] < num]
                if df.shape[0]:
                    outputs.append([filename, df])
            elif idx == len(nums) - 1:
                filename = '{} greater than {}.csv'.format(file_col, num)
                df = DF.loc[DF[col] > num]
                if df.shape[0]:
                    outputs.append([filename, df])
                n1, n2 = nums[idx-1], nums[idx]
                filename = '{} between {} and {}.csv'.format(file_col, n1, n2)
                df = DF.loc[(DF[col] > n1) & (DF[col] < n2)]
                if df.shape[0]:
                    outputs.append([filename, df]) 
            else:
                n1, n2 = nums[idx-1], nums[idx]
                filename = '{} between {} and {}.csv'.format(file_col, n1, n2)
                df = DF.loc[(DF[col] > n1) & (DF[col] < n2)]
                if df.shape[0]:
                    outputs.append([filename, df]) 

            for filename, df in outputs:
                filepath = os.path.join(directory, filename)
                df.to_csv(filepath, encoding='utf-8', index=False)


    # ######################
    # #### MISC Methods ####
    # ######################

    def write_files(self, all=False):
        print('\nWriting File{}...'.format('s' if all else ''))
        filename = 'events_and_adventures.csv'
        base = os.path.join(os.getcwd(), 'output')
        if not os.path.exists(base):
            os.mkdir(base)
        filepath = os.path.join(base, filename)
        self.df.to_csv(filepath, encoding='utf-8', index=False)

        if all:
            df = self.df.copy()
            for col in self.__special_columns:
                print('-> {}'.format(col))
                directory = os.path.join(base, col)
                if os.path.exists(directory):
                    rmtree(directory)
                os.mkdir(directory)

                for item in df[col].dropna().drop_duplicates():
                    if '_from_' in col:
                        self._range_writer(df, col, directory)
                    else:
                        filepath = os.path.join(directory, '{}.csv'.format(item.encode('utf-8', errors='replace')))
                        df.loc[df[col] == item].to_csv(filepath, encoding='utf-8', index=False)


class EAUpdater(object):

    def __init__(self):
        dfpath = os.path.join(os.getcwd(), 'output', 'events_and_adventures.csv')
        if os.path.exists(dfpath):
            self.df = pd.read_csv(dfpath)
        else:
            print('\nNo dataframe found at: {}\nRun EALoader() routine before calling EAUpdater()\n'.format(dfpath))
            sys.exit()
        # self.driver = login()
        self._signup, self._wait, self._cancel = self._get_dataframes()
        self.take_action()


    def _get_dataframes(self):
        files = glob(os.path.join(os.getcwd(), 'output', '*', '*.csv'))
        df = self.df.copy()
        for filepath in files:
            df = df.append(pd.read_csv(filepath))

        signup_not_null = ~df.sign_up.isnull()
        attending_is_null = df.attending.isnull()
        attending_not_null = ~df.attending.isnull()
        event_is_full = df.event_status.str.contains('full')
        event_not_full = ~df.event_status.str.contains('full')
        wait_not_null = ~df.wait_list.isnull()
        cancel_not_null = ~df.cancel.isnull()

        signup = df.loc[(df.event_cost == 0) & (attending_is_null)]
        # signup = df.loc[(signup_not_null) & (attending_is_null) & (event_not_full)].drop_duplicates()
        wait = df.loc[(wait_not_null) & (event_is_full) & (attending_is_null)].drop_duplicates()
        cancel = df.loc[(cancel_not_null) & (attending_not_null)].drop_duplicates()

        # signup = df.loc[
        #     (~df.sign_up.isnull()) &
        #     (df.attending.isnull()) &
        #     (~df.event_status.str.contains('full'))].drop_duplicates()
        # wait = df.loc[
        #     (~df.wait_list.isnull()) &
        #     (df.event_status.str.contains('full')) &
        #     (df.attending.isnull())].drop_duplicates()
        # cancel = df.loc[
        #     (~df.cancel.isnull()) &
        #     (~df.attending.isnull())].drop_duplicates()
        return signup, wait, cancel


    def _override_dataframe(self):
        pass


    def take_action(self):
        driver = login()
        self._event_sign_up(driver)
        # self._event_wait_list(driver)
        # self._event_cancel(driver)
        driver.close()


    def _click_signup(self, driver, url):
        driver.get(url)
        driver.find_element_by_id('contentMain_lnkSignup').click()


    def _calculate_payment_amount(self, event, tax, venue, credit):
        return sum([event, tax, venue]) - credit


    def _find_by_id(self, driver):
        return driver.find_element_by_id


    def _acknowledge_waiver(self, driver):
        waiver = driver.find_element_by_id('contentMain_chkWaiver')
        if waiver.is_enabled():
            if not waiver.is_selected():
                waiver.click()


    def _event_sign_up(self, driver):
        for idx, row in self._signup.iterrows():
            url = row.url
            name = row.event_name
            edate = self._parse_date(row.event_date)

            self._click_signup(driver, url)
            find = self._find_by_id(driver)

            credit, credit_cb = self._parse_cost(find('contentMain_eventcredit').text), find('contentMain_chkPayEC')
            price = self._calculate_payment_amount(
                self._parse_cost(find('contentMain_eventcost').text),
                self._parse_cost(find('contentMain_eventtax').text),
                self._parse_cost(find('contentMain_venuecost').text),
                credit
            )
            if credit and credit_cb.is_enabled():
                if credit_cb.is_selected():
                    credit_cb.click()
                ans = raw_input('Use ${:.2f} event credit? '.format(credit))
                if ans == 'y':
                    credit_cb.click()

            signup = find('contentMain_chkSignup')
            if signup.is_enabled():
                if not signup.is_selected():
                    signup.click()

            self._acknowledge_waiver(driver)

            submit = find('contentMain_btnSubmit')
            if submit.is_enabled():
                ans = raw_input('Pay ${:,.2f} for {}? '.format(price, name))
                if ans == 'y':
                    submit.click()
                    self.df.loc[
                        (self.df.event_name == name) &
                        (self.df.event_date == edate),
                        ['attending', 'sign_up']
                    ] = ['x', None]


    # def _event_wait_list(self, driver):
    #     for idx, row in self._wait.iterrows():
    #         url = row.url
    #         name = row.event_name
    #         edate = self._parse_date(row.event_date)

    #         self._click_signup(driver, url)
    #         find = self._find_by_id(driver)

    #         wait = find('contentMain_chkWaitList')
    #         if wait.is_enabled():
    #             if not wait.is_selected():
    #                 wait.click()

    #         self._acknowledge_waiver(driver)

    #         submit = find('contentMain_btnSubmit')
    #         if submit.is_enabled():
    #             ans = raw_input('Cancel {}? '.format(name))
    #             if ans == 'y':
    #                 # submit.click()
    #                 self.df.loc[
    #                     (self.df.event_name == name) &
    #                     (self.df.event_date == edate),
    #                     ['attending', 'wait_list']
    #                 ] = [None, None]


    # def _event_cancel(self, driver):
    #     for url in self._cancel.url:
    #         self._click_signup(driver, url)
    #         find = self._find_by_id(driver)


    def _parse_cost(self, cost):
        pattern = re.compile(
            r'[$]?(?P<amount>[\d\,]*[.]+[\d]*)'
        )
        try:
            match = re.match(pattern, cost).groupdict()
            return Decimal(match['amount'].replace(',', ''))
        except AttributeError:
            return Decimal('0.00')

    def _parse_date(self, dstr):
        _date, _time = dstr.split(' ')
        month, day, year = _date.split('/')
        year = '20' + year
        hour, minute = _time.split(':')
        res = pd.Timestamp(int(year), int(month), int(day), int(hour), int(minute))
        return res.strftime('%Y-%m-%d %H:%M:%S')


    # sign up page element IDs
    # amount = 'contentMain_eventcost' -> Event Cost
    # amount = 'contentMain_eventtax' -> Event Tax
    # amount = 'contentMain_venuecost' -> Venue Cost
    # amount = 'contentMain_eventcredit' -> Event Credit

    # checkbox = 'contentMain_chkPayEC' -> Event Credit not available Use Event Credit
    # checkbox = 'contentMain_chkSignup' -> I want to signup for this event
    # checkbox = 'contentMain_chkWaitList' -> I want to be Wait Listed for this event
    # checkbox = '' -> I want to cancel my signup for this event
    # checkbox = 'contentMain_chkWaiver' -> Yes, I have read and agree to the waiver and release




def main():
    os.system('clear')
    # ea = EAUpdater()
    ea = EALoader()
    ea.write_files(all=True)



if __name__ == '__main__':
    os.system('clear')
    main()
