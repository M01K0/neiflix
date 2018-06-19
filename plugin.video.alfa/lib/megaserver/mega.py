# -*- coding: utf-8 -*-
#Extraído de la librería de MEGA de richardasaurus

import re
from .crypto import *
import json
import urllib
import urllib2
try:
    from Crypto.PublicKey import RSA
except ImportError:
    from Cryptodome.PublicKey import RSA


def rsa_mega_decrypt(self, ciphertext):
    from Crypto.Math.Numbers import Integer
    
    if not 0 < ciphertext < self._n:
        raise ValueError("Ciphertext too large")
    if not self.has_private():
        raise TypeError("This is not a private key")

    return pow(Integer(ciphertext), self._d, self._n)


class RequestError(Exception):
    pass

class Mega(object):
    def __init__(self, options=None):
        self.schema = 'https'
        self.domain = 'mega.co.nz'
        self.timeout = 160  # max time (secs) to wait for resp from api requests
        self.sid = None
        self.sequence_num = random.randint(0, 0xFFFFFFFF)
        self.request_id = make_id(10)

        if options is None:
            options = {}
        self.options = options

    def login(self, email=None, password=None):
        if email:
            self._login_user(email, password)
        else:
            self.login_anonymous()
        return self

    def _login_user(self, email, password):
        password_aes = prepare_key(str_to_a32(password))
        uh = stringhash(email, password_aes)
        resp = self._api_request({'a': 'us', 'user': email, 'uh': uh})
        # if numeric error code response
        if isinstance(resp, int):
            raise RequestError(resp)
        self._login_process(resp, password_aes)

    def login_anonymous(self):
        master_key = [random.randint(0, 0xFFFFFFFF)] * 4
        password_key = [random.randint(0, 0xFFFFFFFF)] * 4
        session_self_challenge = [random.randint(0, 0xFFFFFFFF)] * 4

        user = self._api_request({
            'a': 'up',
            'k': a32_to_base64(encrypt_key(master_key, password_key)),
            'ts': base64_url_encode(a32_to_str(session_self_challenge) +
                                    a32_to_str(encrypt_key(session_self_challenge, master_key)))
        })

        resp = self._api_request({'a': 'us', 'user': user})
        # if numeric error code response
        if isinstance(resp, int):
            raise RequestError(resp)
        self._login_process(resp, password_key)

    def _login_process(self, resp, password):
        encrypted_master_key = base64_to_a32(resp['k'])
        self.master_key = decrypt_key(encrypted_master_key, password)
        if 'tsid' in resp:
            tsid = base64_url_decode(resp['tsid'])
            key_encrypted = a32_to_str(
                encrypt_key(str_to_a32(tsid[:16]), self.master_key))
            if key_encrypted == tsid[-16:]:
                self.sid = resp['tsid']
        elif 'csid' in resp:
            encrypted_rsa_private_key = base64_to_a32(resp['privk'])
            rsa_private_key = decrypt_key(encrypted_rsa_private_key,
                                          self.master_key)

            private_key = a32_to_str(rsa_private_key)
            self.rsa_private_key = [0, 0, 0, 0]

            for i in range(4):
                offset = ((ord(private_key[0]) * 256 + ord(private_key[1]) + 7) / 8) + 2
                self.rsa_private_key[i] = mpi_to_int(private_key[:offset])
                private_key = private_key[offset:]

            encrypted_sid = mpi_to_int(base64_url_decode(resp['csid']))
            
            try:
                #Pycrypto
                rsa_decrypter = RSA.construct(
                    (self.rsa_private_key[0] * self.rsa_private_key[1],
                     0L, self.rsa_private_key[2], self.rsa_private_key[0],
                     self.rsa_private_key[1]))

                sid = '%x' % rsa_decrypter.key._decrypt(encrypted_sid)
            except ValueError:
                #Pycryptodome
                rsa_decrypter = RSA.construct(
                    (self.rsa_private_key[0] * self.rsa_private_key[1],
                     0L, self.rsa_private_key[2], self.rsa_private_key[0],
                     self.rsa_private_key[1]), consistency_check=False)

                rsa_decrypter.rsa_mega_decrypt = rsa_mega_decrypt.__get__(rsa_decrypter)
                sid = '%x' % rsa_decrypter.rsa_mega_decrypt(encrypted_sid)

            sid = binascii.unhexlify('0' + sid if len(sid) % 2 else sid)
            self.sid = base64_url_encode(sid[:43])

    def _api_request(self, data):
        params = {'id': self.sequence_num}
        self.sequence_num += 1

        if self.sid:
            params.update({'sid': self.sid})

        # ensure input data is a list
        if not isinstance(data, list):
            data = [data]

        url = '{0}://g.api.{1}/cs?{2}'.format(self.schema, self.domain, urllib.urlencode(params))

        res = self._post(url, json.dumps(data))

        json_resp = json.loads(res)

        # if numeric error code response
        if isinstance(json_resp, int):
            raise RequestError(json_resp)
        return json_resp[0]

    def _post(self, url, data):

        import ssl
        from functools import wraps

        def sslwrap(func):
            @wraps(func)
            def bar(*args, **kw):
                kw['ssl_version'] = ssl.PROTOCOL_TLSv1
                return func(*args, **kw)

            return bar

        ssl.wrap_socket = sslwrap(ssl.wrap_socket)

        request = urllib2.Request(url, data=data, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_8_5) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/30.0.1599.101 Safari/537.36"})

        contents = urllib2.urlopen(request).read()

        return contents

    def get_user(self):
        user_data = self._api_request({'a': 'ug'})
        return user_data

    def get_quota(self):
        """
        Get current remaining disk quota in MegaBytes
        """
        json_resp = self._api_request({'a': 'uq', 'xfer': 1})
        # convert bytes to megabyes
        return json_resp['mstrg'] / 1048576

    def get_storage_space(self, giga=False, mega=False, kilo=False):
        """
        Get the current storage space.
        Return a dict containing at least:
          'used' : the used space on the account
          'total' : the maximum space allowed with current plan
        All storage space are in bytes unless asked differently.
        """
        if sum(1 if x else 0 for x in (kilo, mega, giga)) > 1:
            raise ValueError("Only one unit prefix can be specified")
        unit_coef = 1
        if kilo:
            unit_coef = 1024
        if mega:
            unit_coef = 1048576
        if giga:
            unit_coef = 1073741824
        json_resp = self._api_request({'a': 'uq', 'xfer': 1, 'strg': 1})
        return {
            'used': json_resp['cstrg'] / unit_coef,
            'total': json_resp['mstrg'] / unit_coef,
        }

    def get_balance(self):
        """
        Get account monetary balance, Pro accounts only
        """
        user_data = self._api_request({"a": "uq", "pro": 1})
        if 'balance' in user_data:
            return user_data['balance']
