
import os
import re
from datetime import date, datetime
import googlemaps

import requests
from bs4 import BeautifulSoup

# Must have chromedriver.exe in basepath
from selenium import webdriver
from selenium.webdriver.support.ui import Select

import pandas as pd

# Supply info.py with:
# - Google Gecoding API Key
# - Username and Password for Events and Adventures
from info import GOOGLE_MAPS_KEY, EA_USERNAME, EA_PASSWORD


class EALoader(object):

    def __init__(self):
        # Constant Variables
        self._output_fields = ['event_name', 'event_location', 'event_status', 'member_status', 'signup_before',
                               'cancel_before', 'event_date', 'host', 'event_type', 'duration', 'attire',
                               'attendees', 'venue_cost', 'event_cost', 'event_tax', 'street', 'city', 'state',
                               'zip', 'raw_address', 'sitename', 'url', 'sign_up', 
        ]

        self._month_dict = {'January': 1, 'February': 2, 'March': 3, 'April': 4, 'May': 5, 'June': 6, 'July': 7,
                            'August': 8, 'September': 9, 'October': 10, 'November': 11, 'December': 12
        }

        # Regular Expression Patterns
        self._date_regex = re.compile(
            r'[\w]*[\s]+(?P<month>[\w]*)[\s]+(?P<day>[\d]{1,2})[,\s]+(?P<year>[\d]{4})[\s]+(?P<hour>[\d]{1,2})[:]+(?P<minute>[\d]{1,2})[\s]+(?P<sign>[\w]{2})'
        )
        self._amt_regex = re.compile(r'[$]?(?P<amt>[\d]*[.]?[\d]*)')
        self._address_regex = re.compile(r'')

        # Google Map API
        self._map = googlemaps.Client(GOOGLE_MAPS_KEY)

        # Events & Adventures URLs
        self._login_url = 'https://singles.eventsandadventures.com/website/logon.aspx'
        self._url = 'https://singles.eventsandadventures.com/member/{}'

        print('Creating Payload...')
        self._payload = self._parse_payload()
        
        print('Gathering Event Links...')
        self.events = self._get_event_links()

        print('Parsing Data...')
        self.data = self._extract_event_details()
        self.dframe = pd.DataFrame(self.data, columns=self._output_fields)


    def write(self):
        today = date.today()
        filename = 'events_and_adventures_{}{}{}'.format(today.year, today.month, today.day)
        directory = os.path.join(os.getcwd(), 'output')
        if not os.path.exists():
            os.mkdir(directory)
        filepath = os.path.join(directory, filename)
        self.dframe.to_csv(filepath)


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


    def _extract_event_details(self):
        data = list()
        with requests.Session() as session:
            post = session.post(self._login_url, data=self._payload)
            for link in self.events:
                request = session.get(link)
                soup = BeautifulSoup(request.content, 'html.parser')

                event_name, event_location = self._parse_event(soup.find(id='contentMain_eventnamelocation'))
                print('-> {}'.format(event_name))

                event_status = soup.find(id='contentMain_eventstatus').text.strip()
                member_status = soup.find(id='contentMain_signupstatus').text.strip()
                event_date = self._parse_date(soup.find(id='contentMain_datetime').text)
                signup_before = self._parse_date(soup.find(id='contentMain_signupbefore').text)
                cancel_before = self._parse_date(soup.find(id='contentMain_cancelbefore').text)
                host = soup.find(id='contentMain_hosts').text.replace('[Photo]', '').strip()
                event_type = soup.find(id='contentMain_eventtype').text.strip()
                duration = soup.find(id='contentMain_duration').text.strip()
                attire = soup.find(id='contentMain_attire').text.strip()
                attendees = soup.find(id='contentMain_memberlimit').text.strip()
                venue_cost = soup.find(id='contentMain_venuecost').text.strip()
                event_cost = soup.find(id='contentMain_eventcost').text.strip()
                event_tax = soup.find(id='contentMain_eventtax').text.strip()
                
                address = soup.find(id='contentMain_venueaddress').text.strip()
                street, city, state, code = self._parse_address(address)
                if street or city or state:
                    address = None
                
                sitename = soup.find(id='contentMain_sitename').text.strip()
                
                items = (event_name, event_location, event_status, member_status, signup_before, cancel_before, event_date,
                         host, event_type, duration, attire, attendees, venue_cost, event_cost, event_tax, street,
                         city, state, code, address, sitename, link, None)
                data.append(items)
        return data


    def _get_event_links(self):
        # Login to Events and Adventures
        driver = webdriver.Chrome('./chromedriver')
        driver.get(self._login_url)
        username = driver.find_element_by_id('contentMain_username')
        username.send_keys('brandon.jaus@gmail.com')
        password = driver.find_element_by_id('contentMain_password')
        password.send_keys('Afgh@n!st@n20!!')
        submit = driver.find_element_by_id('contentMain_btnSubmit')
        submit.click()
        
        # Get to the Calendar
        cal = driver.find_element_by_id('PublicNav1_lnkCalendar')
        cal.click()
        
        # Parse events for current month
        event_links = []
        events = driver.find_elements_by_class_name('calevent')
        for event in events:
            event_links.append(event.get_attribute('href'))


        # Parse events for next month
        today = date.today()
        year, month = today.year, today.month
        month += 1
        if month > 12:
            month %= 12
            year += 1
        select = Select(driver.find_element_by_id('contentMain_lstmonths'))
        select.select_by_value('{}/1/{}'.format(month, year))
        events = driver.find_elements_by_class_name('calevent')
        for event in events:
            link = event.get_attribute('href')
            event_links.append(link)

        driver.close()
        return event_links


    def _parse_date(self, dstr):
        match = re.findall(self._date_regex, dstr)
        month, day, year, hour, minute, sign = match[0]
        month, day, year, hour, minute =  (self._month_dict[month], int(day), int(year),
                                           int(hour), int(minute))
        if sign.lower() == 'pm' and hour != 12:
            hour += 12
        return datetime(year, month, day, hour, minute)


    def _parse_event(self, tag):
        location = tag.find('br').text
        name = tag.text.replace(location, '')
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


    def _get_soup(self, url):
        with requests.Session() as session:
            post = session.post(self._login_url, data=self._payload)
            return BeautifulSoup(session.get(url).content, 'html.parser')


def main():
    ea = EALoader()


if __name__ == '__main__':
    os.system('clear')
    main()
