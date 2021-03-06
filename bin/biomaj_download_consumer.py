'''
Message consumer for download requests
'''

import os
import logging

import requests
import yaml
import consul

from biomaj_download.downloadservice import DownloadService

config_file = 'config.yml'
if 'BIOMAJ_CONFIG' in os.environ:
        config_file = os.environ['BIOMAJ_CONFIG']

config = None
with open(config_file, 'r') as ymlfile:
    config = yaml.load(ymlfile)


def on_download(bank, downloaded_files):
    metrics = []
    if not downloaded_files:
        metric = {'bank': bank, 'error': 1}
        metrics.append(metrics)
    else:
        for downloaded_file in downloaded_files:
            metric = {'bank': bank}
            if 'error' in downloaded_file and downloaded_file['error']:
                metric['error'] = 1
            else:
                metric['size'] = downloaded_file['size']
                metric['download_time'] = downloaded_file['download_time']
            metrics.append(metric)
        r = requests.post(config['web']['local_endpoint'] + '/api/download/metrics', json = metrics)


download = DownloadService(config_file)
download.on_download_callback(on_download)
download.wait_for_messages()
