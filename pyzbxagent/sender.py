# -*- coding: utf-8 -*-
#
# Copyright 2011 Enrico Tröger <enrico(dot)troeger(at)uvena(dot)de>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
# MA 02110-1301, USA.


from pyzbxagent.logger import get_logger
from time import time
from json import dumps, loads
from struct import pack, unpack
from socket import socket, AF_INET, SOCK_STREAM


# Zabbix sender protocol header
ZBX_HEADER = '''ZBXD\1'''


########################################################################
class Sender(object):
    """"""

    #----------------------------------------------------------------------
    def __init__(self, server, port, socket_timeout, send_interval, simulate, database):
        self._server = server
        self._port = port
        self._socket_timeout = socket_timeout
        self._database = database
        self._send_interval = send_interval
        self._simulate = simulate
        self._process_date = None
        self._next_send_date = time()
        self._logger = get_logger()
        self._host = 'makem.l8failed.net'

    #----------------------------------------------------------------------
    def send(self, process_date=None, force=False):
        self._store_process_date(process_date)
        if force or self._sending_data_is_due():
            self._update_next_send_date()
            forced = ' (forced)' if force else ''
            self._logger.debug('Send data to Zabbix server%s' % forced)
            self._send_pending_data()

    #----------------------------------------------------------------------
    def _store_process_date(self, process_date):
        if not process_date:
            self._process_date = time()
        else:
            self._process_date = process_date

    #----------------------------------------------------------------------
    def _sending_data_is_due(self):
        return self._process_date >= self._next_send_date

    #----------------------------------------------------------------------
    def _update_next_send_date(self):
        self._next_send_date = self._process_date + self._send_interval

    #----------------------------------------------------------------------
    def _send_pending_data(self):
        try:
            self._try_to_send_pending_data()
        except Exception, e:
            self._logger.warn('An error occurred while sending items: %s' % e, exc_info=True)

    #----------------------------------------------------------------------
    def _try_to_send_pending_data(self):
        chunk_size = 200
        items = self._database.query_pending_items()

        #~ items = items[:5]
        # process items in 'chunk_size' chunks
        # (similar to zabbix_sender which processes 250 items at once)
        while items:
            self._send_items(items[:chunk_size])
            del items[:chunk_size]

    #----------------------------------------------------------------------
    def _send_items(self, items):
        try:
            result = self._try_to_send_items(items)
        except Exception:
            raise
        else:
            # clean up
            self._database.delete_items(items)
            processed = self._clean_processed_message(result['info'])
            self._logger.info('Processed a chunk of %s items (%s)' % (len(items), processed))

    #----------------------------------------------------------------------
    def _try_to_send_items(self, items):
        request_data = list()
        for item in items:
            request_item = dict(
                host=self._host,
                key=item['key'],
                value=item['value'],
                clock=item['entry_date'])
            request_data.append(request_item)

        # build the request
        request = dict(request='sender data', clock=self._process_date, data=request_data)
        request = dumps(request)
        return self._send_request(request)

    #----------------------------------------------------------------------
    def _send_request(self, request):
        if self._simulate:
            return dict(info='Processed 0 Failed 0 Total 0 Seconds spent 0')

        request_length = len(request)
        request_header = '%s\0\0\0\0' % pack('i', request_length)

        data_to_send = '%s%s%s' % (ZBX_HEADER, request_header, request)

        # open socket
        sock = socket(AF_INET, SOCK_STREAM)
        sock.settimeout(self._socket_timeout)
        sock.connect((self._server, self._port))

        # send the data to the server
        sock.sendall(data_to_send)

        # read its response, the first five bytes are the header again
        response_header = sock.recv(5)
        if not response_header == ZBX_HEADER:
            raise ValueError('Invalid response')

        # read the data header to get the length of the response
        response_data_header = sock.recv(8)
        response_data_header = response_data_header[:4] # first four bytes are response length
        response_len = unpack('i', response_data_header)[0]

        # read the whole rest of the response now that we know the length
        response_raw = sock.recv(response_len)

        sock.close()

        response = loads(response_raw)
        if response['response'] != 'success':
            raise ValueError('Invalid response: %s: %s' % (response['response'], response['info']))

        return response

    #----------------------------------------------------------------------
    def _clean_processed_message(self, result):
        end = result.find(' Seconds')
        return result[:end]
