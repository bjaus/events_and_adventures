
import os
import re
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


class EALoader(object):

    def __init__(self):
        # Constant Variables
        self._two_decimals = Decimal('0.01')
        self._special_columns = ['event_status', 'member_status', 'sitename', 'city', 'state', 'event_day', 'dist_from_home', 
                                 'dist_from_work', 'time_from_work', 'time_from_home',]
        self._output_fields = ['attending', 'sign_up', 'wait_list', 'cancel', 'event_name', 'event_location', 'event_status',
                                'member_status', 'signup_before', 'cancel_before', 'event_date', 'event_day', 'host', 'event_type',
                                'duration', 'attire', 'attendees', 'venue_cost', 'event_cost', 'event_tax', 'dist_from_home',
                                'time_from_home', 'dist_from_work', 'time_from_work', 'street', 'city', 'state', 'zip',
                                'raw_address', 'sitename', 'url', 
        ]

        self._month_dict = {'January': 1, 'February': 2, 'March': 3, 'April': 4, 'May': 5, 'June': 6, 'July': 7,
                            'August': 8, 'September': 9, 'October': 10, 'November': 11, 'December': 12
        }

        self._weekday_dict = {0: 'Monday', 1: 'Tuesday', 2: 'Wednesday', 3: 'Thursday', 4: 'Friday', 5: 'Saturday', 6: 'Sunday'}

        # Regular Expression Patterns
        self._date_regex = re.compile(
            r'[\w]*[\s]+(?P<month>[\w]*)[\s]+(?P<day>[\d]{1,2})[,\s]+(?P<year>[\d]{4})[\s]+(?P<hour>[\d]{1,2})[:]+(?P<minute>[\d]{1,2})[\s]+(?P<sign>[\w]{2})'
        )
        self._amt_regex = re.compile(r'[$]?(?P<amt>[\d]*[.]?[\d]*)')
        self._address_regex = re.compile(r'')

        # Google Map API
        self._map = googlemaps.Client(GOOGLE_MAPS_KEY)

        # Events & Adventures URL
        self._login_url = 'https://singles.eventsandadventures.com/website/logon.aspx'

        self._payload = self._parse_payload()
        
        print('\nGathering Event Links...')
        self._events = self._get_event_links()

        print('\nParsing Data...')
        self._data = self._extract_event_details()

        print('\nProducing DataFrame...')
        self.dframe = self._produce_dataframe()


    def _parse_payload(self):
        res = requests.get(self._login_url)
        soup = BeautifulSoup(res.content, 'html.parser')
        form = soup.find('form')
        payload = dict()
        for inp in form.find_all('input'):
            _id = inp.get('id')
            name = inp.get('name')
            if 'username' in _id:
                payload.update({name: EA_USERNAME})
            elif 'password' in _id:
                payload.update({name: EA_PASSWORD})
            else:
                payload.update({name: inp.get('value')})
        return payload


    def _get_event_links(self):
        # Login to Events and Adventures
        driver = webdriver.Chrome('./chromedriver')
        driver.get(self._login_url)
        username = driver.find_element_by_id('contentMain_username')
        username.send_keys(EA_USERNAME)
        password = driver.find_element_by_id('contentMain_password')
        password.send_keys(EA_PASSWORD)
        submit = driver.find_element_by_id('contentMain_btnSubmit')
        submit.click()
        
        # Get to the Calendar

        
        # Parse events for current month
        event_links = []
        events = driver.find_elements_by_class_name('calevent')
        for event in events:
            event_links.append(event.get_attribute('href'))


        # Parse events for next month
        year, month = self._produce_date(1)
        select = Select(driver.find_element_by_id('contentMain_lstmonths'))
        select.select_by_value('{}/1/{}'.format(month, year))
        events = driver.find_elements_by_class_name('calevent')
        for event in events:
            link = event.get_attribute('href')
            event_links.append(link)

        # Parse events two months out
        year, month = self._produce_date(2)
        select = Select(driver.find_element_by_id('contentMain_lstmonths'))
        select.select_by_value('{}/1/{}'.format(month, year))
        events = driver.find_elements_by_class_name('calevent')
        for event in events:
            link = event.get_attribute('href')
            event_links.append(link)

        driver.close()
        return event_links


    def _produce_dataframe(self):
        df = pd.DataFrame(self._data, columns=self._output_fields)

        df.loc[df.member_status.str.strip().str.lower() == 'you are signed up', 'attending'] = 'X'
        df = df.sort_values(by=['sitename', 'member_status', 'event_status', 'signup_before', 'event_cost', 'event_date'],
                            ascending=[True, True, True, True, True, True])

        return df


    def _extract_event_details(self):
        data = list()
        with requests.Session() as session:
            post = session.post(self._login_url, data=self._payload)
            for link in self._events:
                request = session.get(link)
                soup = BeautifulSoup(request.content, 'html.parser')

                event_name, event_location = self._parse_event(soup.find(id='contentMain_eventnamelocation'))
                if 'New Member' in event_name:
                    continue

                event_date = self._parse_date(soup.find(id='contentMain_datetime').text)
                if event_date < datetime.now():
                    # print('-> {:>9}: {}'.format('Passed', event_name))
                    continue

                event_status = soup.find(id='contentMain_eventstatus').text.strip().encode('utf-8')
                if event_status.lower() == 'event has passed':
                    # print('-> {:>9}: {}'.format('Passed', event_name))
                    continue

                member_status = soup.find(id='contentMain_signupstatus').text.strip()
                if member_status.lower() == 'you canceled':
                    # print('-> {:>9}: {}'.format('Cancelled', event_name))
                    continue

                signup_before = self._parse_date(soup.find(id='contentMain_signupbefore').text)
                if signup_before < datetime.now():
                    # print('-> {:>9}: {}'.format('Closed', event_name))
                    continue

                if 'full' in event_status.lower():
                    print '-> {:>9}: {}'.format('Full', event_name)
                elif member_status.lower().strip() == 'you are signed up':
                    print '-> {:>9}: {}'.format('Signed Up', event_name)
                else:
                    print('-> {:>9}: {}'.format('Available', event_name))

                event_day = self._weekday_dict.get(event_date.weekday(), None)
                cancel_before = self._parse_date(soup.find(id='contentMain_cancelbefore').text)
                host = soup.find(id='contentMain_hosts').text.replace('[Photo]', '').strip()
                event_type = soup.find(id='contentMain_eventtype').text.strip()
                duration = soup.find(id='contentMain_duration').text.strip()
                attire = soup.find(id='contentMain_attire').text.strip()

                # Get current number of people signed up
                attendees = None # Found on Sign Up Page
                attendee_limit = soup.find(id='contentMain_memberlimit').text.strip()
                spots_left = None # attendees - attendee_limit (if limited) else None

                # Verify cost on Signup Page
                venue_cost = soup.find(id='contentMain_venuecost').text.strip()
                event_cost = soup.find(id='contentMain_eventcost').text.strip()
                event_tax = soup.find(id='contentMain_eventtax').text.strip()
                
                address = soup.find(id='contentMain_venueaddress').text.strip()

                # Use Google Maps API to determine distance
                dist_from_home, time_from_home = self._extract_travel_data(HOME, address)
                dist_from_work, time_from_work = self._extract_travel_data(WORK, address)

                street, city, state, code = self._parse_address(address)
                if street or city or state:
                    address = None
                
                sitename = soup.find(id='contentMain_sitename').text.strip()
                
                items = (None, None, None, None, event_name, event_location, event_status, member_status, signup_before,
                         cancel_before, event_date, event_day, host, event_type, duration, attire, attendees, venue_cost,
                         event_cost, event_tax, dist_from_home, time_from_home, dist_from_work, time_from_work, street,
                         city, state, code, address, sitename, link)
                data.append(items)
        return data


    ########################
    #### Parser Methods ####
    ########################

    def _parse_date(self, dstr):
        match = re.findall(self._date_regex, dstr)
        month, day, year, hour, minute, sign = match[0]
        month, day, year, hour, minute =  (self._month_dict[month], int(day), int(year),
                                           int(hour), int(minute))
        if sign.lower() == 'pm' and hour != 12:
            hour += 12
        return datetime(year, month, day, hour, minute)


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
        return (km * Decimal('0.62137')).quantize(self._two_decimals)


    def _add_numeric_travel_data(self, df):
        dist_regex = re.compile(r'(?P<amount>[\d,]*[.]?[\d]*) miles')
        time_regex = re.compile(r'(?P<amount>[\d,]*[.]?[\d]*) minutes')

        df['dist_from_home_num'] = df.dist_from_home\
                                   .str.extract(pat=dist_regex, expand=False)\
                                   .str.replace(',', '').astype(float, na=False)
        df['time_from_home_num'] = df.time_from_home\
                                   .str.extract(pat=time_regex, expand=False)\
                                   .str.replace(',', '').astype(float, na=False)
        
        df['dist_from_work_num'] = df.dist_from_work\
                                   .str.extract(pat=dist_regex, expand=False)\
                                   .str.replace(',', '').astype(float, na=False)
        df['time_from_work_num'] = df.time_from_work\
                                   .str.extract(pat=time_regex, expand=False)\
                                   .str.replace(',', '').astype(float, na=False)

        return df

    def _range_writer(self, df, col, directory):
        nums = [10, 15, 20, 25, 30, 40, 50, 60, 100]
        DF = df.copy()
        file_col = col.replace('_', ' ')
        col_num = '{}_num'.format(col)
        for idx, num in enumerate(nums):
            outputs = []
            if idx == 0:
                filename = '{} less than {}.csv'.format(file_col, num)
                df = DF.loc[DF[col_num] < num]
                if df.shape[0]:
                    outputs.append([filename, df])
            elif idx == len(nums) - 1:
                filename = '{} greater than {}.csv'.format(file_col, num)
                df = DF.loc[DF[col_num] > num]
                if df.shape[0]:
                    outputs.append([filename, df])
                n1, n2 = nums[idx-1], nums[idx]
                filename = '{} between {} and {}.csv'.format(file_col, n1, n2)
                df = DF.loc[(DF[col_num] > n1) & (DF[col_num] < n2)]
                if df.shape[0]:
                    outputs.append([filename, df]) 
            else:
                n1, n2 = nums[idx-1], nums[idx]
                filename = '{} between {} and {}.csv'.format(file_col, n1, n2)
                df = DF.loc[(DF[col_num] > n1) & (DF[col_num] < n2)]
                if df.shape[0]:
                    outputs.append([filename, df]) 

            for filename, df in outputs:
                filepath = os.path.join(directory, filename)
                df.iloc[:, 4:-4].to_csv(filepath, encoding='utf-8', index=False)


    ######################
    #### MISC Methods ####
    ######################

    def write_files(self, all=False):
        print('\nWriting Files...')
        filename = 'events_and_adventures.csv'
        base = os.path.join(os.getcwd(), 'output')
        if not os.path.exists(base):
            os.mkdir(base)
        filepath = os.path.join(base, filename)
        self.dframe.to_csv(filepath, encoding='utf-8', index=False)

        if all:
            df = self._add_numeric_travel_data(self.dframe.copy())
            for col in self._special_columns:
                print('-> {}'.format(col))
                directory = os.path.join(base, col)
                if os.path.exists(directory):
                    rmtree(directory)
                os.mkdir(directory)

                for item in df[col].dropna().drop_duplicates():
                    if '_from_' in col:
                        self._range_writer(df, col, directory)
                    else:
                        filepath = os.path.join(directory, '{}.csv'.format(item))
                        df.iloc[:, 4:-4].loc[df[col] == item].to_csv(filepath, encoding='utf-8', index=False)


    def _get_soup(self, url):
        with requests.Session() as session:
            post = session.post(self._login_url, data=self._payload)
            return BeautifulSoup(session.get(url).content, 'html.parser')


def main():
    ea = EALoader()
    ea.write_files(all=True)


if __name__ == '__main__':
    os.system('clear')
    main()
