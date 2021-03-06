from biomaj_download.downloadservice import DownloadService
import requests
import logging
import uuid
import time

import pika

from Queue import Queue
from biomaj_download.download.downloadthreads import DownloadThread
from biomaj_download.message import message_pb2


class DownloadClient(DownloadService):

    def __init__(self, rabbitmq_host, pool_size=5):
        self.logger = logging
        self.channel = None
        self.pool_size = pool_size
        self.proxy = None
        self.bank = None
        if rabbitmq_host:
            self.remote = True
            connection = pika.BlockingConnection(pika.ConnectionParameters(rabbitmq_host))
            self.channel = connection.channel()
        else:
            self.remote = False
        self.logger.info("Use remote: %s" % (str(self.remote)))
        self.download_pool = []
        self.files_to_download = 0

    def set_queue_size(self, size):
        self.pool_size = size

    def create_session(self, bank, proxy=None):
        self.bank = bank
        if not self.remote:
            self.session = str(uuid.uuid4())
            return self.session
        r = requests.post(proxy + '/api/download/session/' + bank)
        if not r.status_code == 200:
            raise Exception('Failed to connect to the download proxy')
        result = r.json()
        self.session = result['session']
        self.proxy = proxy
        return result['session']

    def download_status(self):
        # If biomaj_proxy
        r = requests.get(self.proxy + '/api/download/status/download/' + self.bank + '/' + self.session)
        if not r.status_code == 200:
            raise Exception('Failed to connect to the download proxy')
        result = r.json()
        return (result['progress'], result['errors'])

    def download_remote_files(self, cf, downloaders, offline_dir):
        '''
        cf = Config
        downloaders = list of downloader
        offline_dir = base dir to download files

        '''
        for downloader in downloaders:
            for file_to_download in downloader.files_to_download:
                operation = message_pb2.Operation()
                operation.type = 1
                message = message_pb2.DownloadFile()
                message.bank = self.bank
                message.session = self.session
                message.local_dir = offline_dir
                remote_file = message_pb2.DownloadFile.RemoteFile()
                protocol = downloader.protocol
                remote_file.protocol = message_pb2.DownloadFile.Protocol.Value(protocol.upper())
                remote_file.server = downloader.server
                if cf.get('remote.dir'):
                    remote_file.remote_dir = cf.get('remote.dir')
                else:
                    remote_file.remote_dir = ''
                remote_file.credentials  = downloader.credentials
                biomaj_file = remote_file.files.add()
                biomaj_file.name = file_to_download['name']
                if 'root' in file_to_download and file_to_download['root']:
                    biomaj_file.root = file_to_download['root']
                if 'param' in file_to_download and file_to_download['param']:
                    for key in list(file_to_download['param'].keys()):
                        param = remote_file.param.add()
                        param.name = key
                        param.value = file_to_download['param'][key]
                if 'save_as' in file_to_download and file_to_download['save_as']:
                    biomaj_file.save_as = file_to_download['save_as']
                if 'url' in file_to_download and file_to_download['url']:
                    biomaj_file.url = file_to_download['url']
                if 'permissions' in file_to_download and file_to_download['permissions']:
                    biomaj_file.metadata.permissions = file_to_download['permissions']
                if 'size' in file_to_download and file_to_download['size']:
                    biomaj_file.metadata.size = file_to_download['size']
                if 'year' in file_to_download and file_to_download['year']:
                    biomaj_file.metadata.year = file_to_download['year']
                if 'month' in file_to_download and file_to_download['month']:
                    biomaj_file.metadata.month = file_to_download['month']
                if 'day' in file_to_download and file_to_download['day']:
                    biomaj_file.metadata.day = file_to_download['day']
                if 'hash' in file_to_download and file_to_download['hash']:
                    biomaj_file.metadata.hash = file_to_download['hash']
                if 'md5' in file_to_download and file_to_download['md5']:
                    biomaj_file.metadata.md5 = file_to_download['md5']

                message.http_method = message_pb2.DownloadFile.HTTP_METHOD.Value(downloader.method.upper())

                timeout_download = cf.get('timeout.download', None)
                if timeout_download:
                    message.timeout_download = timeout_download

                message.remote_file.MergeFrom(remote_file)
                operation.download.MergeFrom(message)
                self.download_remote_file(operation)

    def download_remote_file(self, operation):
        # If biomaj_proxy
        self.files_to_download += 1
        if self.remote:
            self.ask_download(operation)
        else:
            self.download_pool.append(operation.download)

    def _download_pool_files(self):
        thlist = []

        logging.info("Workflow:wf_download:Download:Threads:FillQueue")

        message_queue = Queue()
        for message in self.download_pool:
            message_queue.put(message)

        logging.info("Workflow:wf_download:Download:Threads:Start")

        for i in range(self.pool_size):
            th = DownloadThread(self, message_queue)
            thlist.append(th)
            th.start()

        message_queue.join()

        logging.info("Workflow:wf_download:Download:Threads:Over")
        nb_error = 0
        nb_files_to_download = 0
        for th in thlist:
            nb_files_to_download += th.files_to_download
            if th.error > 0:
                nb_error += 1
        return nb_error

    def wait_for_download(self):
        over = False
        nb_files_to_download = self.files_to_download
        logging.info("Workflow:wf_download:RemoteDownload:Waiting")
        if self.remote:
            download_error = False
            while not over:
                (progress, error) = self.download_status()
                if progress == nb_files_to_download:
                    over = True
                    logging.info("Workflow:wf_download:RemoteDownload:Completed:" + str(progress))
                    logging.info("Workflow:wf_download:RemoteDownload:Errors:" + str(error))
                else:
                    if progress % 10 == 0:
                        logging.info("Workflow:wf_download:RemoteDownload:InProgress:" + str(progress) + '/' + nb_files_to_download)
                    time.sleep(1)
                if error > 0:
                    download_error = True
                    r = requests.get(self.proxy + '/api/download/error/download/' + self.bank + '/' + self.session)
                    if not r.status_code == 200:
                        raise Exception('Failed to connect to the download proxy')
                    result = r.json()
                    for err in result['error']:
                        logging.info("Workflow:wf_download:RemoteDownload:Errors:Info:" + str(err))
            return download_error
        else:
            error = self._download_pool_files()
            logging.info('Workflow:wf_download:RemoteDownload:Completed')
            if error > 0:
                logging.info("Workflow:wf_download:RemoteDownload:Errors:" + str(error))
                return True
            else:
                return False

    def clean(self):
        if self.remote:
            r = requests.delete(self.proxy + '/api/download/session/' + self.bank + '/' + self.session)
            if not r.status_code == 200:
                raise Exception('Failed to connect to the download proxy')
