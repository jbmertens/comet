"""CoMeT dataset manager."""

import datetime
import inspect
import logging
import requests
import json

# Endpoint names:
REGISTER_STATE = '/register-state'
REGISTER_DATASET = '/register-dataset'
SEND_STATE = '/send-state'
REGISTER_EXTERNAL_STATE = '/register-external-state'

TIMESTAMP_FORMAT = '%Y-%m-%d-%H:%M:%S.%f'


class CometError(BaseException):
    """Base class for all comet exceptions."""

    pass


class ManagerError(CometError):
    """There was an internal error in dataset management."""

    pass


class BrokerError(CometError):
    """There was an error registering states or datasets with the broker."""

    pass


class Manager:
    """
    CoMeT dataset manager.

    An interface for the CoMeT dataset broker. State changes are passed to the manager,
    it takes care of registering everything with the broker and keeps local copies to reduce
    network traffic.
    """

    def __init__(self, host, port, noconfig=False):
        """Set up the CoMeT dataset manager.

        Parameters
        ----------
        host : str
            Dataset broker host.
        port : int
            Dataset broker port.
        """
        self.broker = 'http://{}:{}'.format(host, port)
        self.config_state = None
        self.start_state = None
        self.states = dict()
        logging.basicConfig()
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.DEBUG)

    def register_start(self, start_time, version):
        """Register a startup with the broker.

        This should never be called twice with different parameters. If you want to register a
        state that may change, use :function:`register_state` instead.

        This does not attach the state to a dataset. If that's what you want to do, use
        :function:`register_state` instead.

        Parameters
        ----------
        start_time : :class:`datetime.datetime`
            The time in UTC when the program was started.
        version : str
            A unique string identifying the version of this software. This should include version
            tags, and if applicable git commit hashes as well as the "dirty" state of the local
            working tree.

        Raises
        ------
        :class:`ManagerError`
            If there was an internal error in the dataset management. E.g. if this was called
            already before. This is to register a startup. Once.
        :class:`BrokerError`
            If there was an error in registering stuff with the broker.
        :class:`ConnectionError`
            If the broker can't be reached.

        """
        if not isinstance(start_time, datetime.datetime):
            raise ManagerError("start_time needs to be of type 'datetime.datetime' (is {})."
                               .format(type(start_time).__name__))
        if not isinstance(version, str):
            raise ManagerError("version needs to be of type 'str' (is {})."
                               .format(type(start_time).__name__))
        if self.start_state:
            raise ManagerError('A startup was already registered, this can only be done once.')

        # get name of callers module
        name = inspect.getmodule(inspect.stack()[1][0]).__name__
        if name == '__main__':
            name = inspect.getmodule(inspect.stack()[1][0]).__file__
        self.logger.info('Registering startup for {}.'.format(name))

        state = {'type': 'start', 'name': name, 'time': start_time.strftime(TIMESTAMP_FORMAT),
                 'version': version}
        state_id = self._make_hash(state)

        request = {'hash': state_id}
        reply = self._send(REGISTER_STATE, request)

        # Does the broker ask for the state?
        if reply.get('request') == 'get_state':
            if reply.get('hash') != state_id:
                raise BrokerError('The broker is asking for state {} when state {} (start) was '
                                  'registered.'.format(reply.get('hash'), state_id))
            self._send_state(state_id, state)

        self.states[state_id] = state
        self.start_state = state_id

        return

    def register_config(self, config):
        """Register a static config with the broker.

        This should just be called once on start. If you want to register a state that may change,
        use :function:`register_state` instead.

        This does not attach the state to a dataset. If that's what you want to do, use
        :function:`register_state` instead.

        Parameters
        ----------
        config : dict
            The config should be JSON-serializable, preferably a dictionary.

        Raises
        ------
        :class:`ManagerError`
            If there was an internal error in the dataset management.
        :class:`BrokerError`
            If there was an error in registering stuff with the broker.
        :class:`ConnectionError`
            If the broker can't be reached.

        """
        if not isinstance (config, dict):
            raise ManagerError('config needs to be a dictionary (is `{}`).'
                               .format(type(config).__name__))
        if not self.start_state:
            raise ManagerError("Start has to be registered before config "
                               "(use 'register_start()').")

        # get name of callers module
        name = inspect.getmodule(inspect.stack()[1][0]).__name__
        if name == '__main__':
            name = inspect.getmodule(inspect.stack()[1][0]).__file__
        self.logger.info('Registering config for {}.'.format(name))

        config['type'] = 'config'
        config['name'] = name
        state_id = self._make_hash(config)

        request = {'hash': state_id}
        reply = self._send(REGISTER_STATE, request)

        # Does the broker ask for the state?
        if reply.get('request') == 'get_state':
            if reply.get('hash') != state_id:
                raise BrokerError('The broker is asking for state {} when state {} (config) '
                                  'was registered.'.format(reply.get('hash'), state_id))
            request['state'] = config
            self._send(SEND_STATE, request)

        self.states[state_id] = config
        self.config_state = state_id

        return

    def _send(self, endpoint, data):
        try:
            reply = requests.post(self.broker + endpoint, data=json.dumps(data))
            reply.raise_for_status()
        except requests.exceptions.ConnectionError:
            raise BrokerError('Failure connecting to comet.broker at {}{}: make sure it is '
                              'running.'.format(self.broker, endpoint))
        reply = reply.json()
        self._check_result(reply.get('result'), endpoint)
        return reply

    def _send_state(self, state_id, state):
        self.logger.debug('sending state {}'.format(state_id))
        request = {'hash': state_id, 'state': state}
        self._send(SEND_STATE, request)

    def _check_result(self, result, endpoint):
        if result != 'success':
            raise BrokerError('The {}{} answered with result `{}`, expected `success`.'
                              .format(self.broker, endpoint, result))

    @staticmethod
    def _make_hash(data):
        return hash(json.dumps(data, sort_keys=True))

    def get_state(self, type=None, dataset_id=None):
        """
        Get the static config or the dataset state that was added last.

        If called without parameters, this returns the static config that was registered.

        Note
        ----
        This only finds state that where registered locally with the comet manager.

        TODO
        ----
        Ask the broker for unknown states.

        Parameters
        ----------
        type : str
            The name of the type of the state to return. If this is `None`, the initial config will
            be returned. Default: `None`.
        dataset_id : int
            The ID of the dataset the last state of given type should be returned for. If this is
            `None` the dataset hirarchy is assumed to not have multiple branches. Default: `None`.

        Returns
        -------
        dict
            The last config or state of requested type that was registered. Returns `None` if
            requested state not found.
        """
        if type is None:
            return self.states[self.config_state]

        raise ManagerError('get_state: Not implemented for anything but config.')
