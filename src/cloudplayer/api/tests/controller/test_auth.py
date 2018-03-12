import datetime
import json

import mock
import pytest
import tornado.gen

from cloudplayer.api.controller.auth import (
    create_controller, AuthController,
    SoundcloudAuthController, YoutubeAuthController)


@pytest.mark.parametrize('id_, class_', [
    ('soundcloud', SoundcloudAuthController),
    ('youtube', YoutubeAuthController)])
def test_create_controller_should_return_correct_controller(id_, class_):
    controller = create_controller(id_, mock.Mock())
    assert isinstance(controller, class_)


def test_create_controller_should_reject_invalid_provider_id():
    with pytest.raises(ValueError):
        create_controller('not-a-provider', mock.Mock())


class CloudplayerController(AuthController):
    PROVIDER_ID = 'cloudplayer'
    OAUTH_ACCESS_TOKEN_URL = 'cp://auth'
    API_BASE_URL = 'cp://base-api'
    OAUTH_TOKEN_PARAM = 'token-param'
    OAUTH_CLIENT_KEY = 'client-key'


def test_auth_controller_should_provide_instance_args(db, current_user):
    controller = CloudplayerController(db, current_user)
    assert controller.db is db
    assert controller.current_user == current_user
    assert controller.settings == {
        'api_key': 'cp-api-key', 'key': 'cp-key', 'secret': 'cp-secret'}
    assert controller.account.id == current_user['cloudplayer']


def test_auth_controller_should_check_token_expiration_before_refresh(
        db, current_user):
    controller = CloudplayerController(db, current_user)
    a_second_ago = datetime.datetime.utcnow() - datetime.timedelta(seconds=1)
    controller.account.token_expiration = a_second_ago
    assert controller.should_refresh is True
    in_an_hour = datetime.datetime.utcnow() + datetime.timedelta(hours=1)
    controller.account.token_expiration = in_an_hour
    assert controller.should_refresh is False


@pytest.mark.gen_test
def test_auth_controller_should_reset_account_on_refresh_error(
        db, current_user):

    ages_ago = datetime.datetime(2015, 7, 14, 12, 30)
    controller = CloudplayerController(db, current_user)
    account = controller.account
    account.access_token = 'stale-access-token'
    account.refresh_token = 'stale-refresh-token'
    account.token_expiration = ages_ago

    @tornado.gen.coroutine
    def fetch(*_, **kw):
        response = mock.Mock()
        response.error = True
        return response

    with mock.patch.object(controller.http_client, 'fetch', fetch):
        yield controller.refresh()

    assert account.access_token is None
    assert account.refresh_token is None
    assert account.token_expiration is not ages_ago


@pytest.mark.parametrize('body, expect', [
    ({
        'access_token': 'new-access-token'
    }, {
        'access_token': 'new-access-token',
        'refresh_token': 'old-refresh-token',
        'token_expiration': datetime.datetime(2015, 7, 14, 12, 30)
    }),
    ({
        'access_token': 'new-access-token',
        'refresh_token': 'new-refresh-token'
    }, {
        'access_token': 'new-access-token',
        'refresh_token': 'new-refresh-token',
        'token_expiration': datetime.datetime(2015, 7, 14, 12, 30)
    }),
    ({
        'access_token': 'new-access-token',
        'refresh_token': 'new-refresh-token',
        'expires_in': 300,
    }, {
        'access_token': 'new-access-token',
        'refresh_token': 'new-refresh-token',
        'token_expiration': (
            datetime.datetime.utcnow() + datetime.timedelta(seconds=300))
    })
])
@pytest.mark.gen_test
def test_auth_controller_should_refresh_access_token(
        db, current_user, body, expect):

    controller = CloudplayerController(db, current_user)
    account = controller.account
    account.access_token = 'old-access-token'
    account.refresh_token = 'old-refresh-token'
    account.token_expiration = datetime.datetime(2015, 7, 14, 12, 30)

    @tornado.gen.coroutine
    def fetch(*_, **kw):
        response = mock.Mock()
        response.error = None
        response.body = json.dumps(body)
        return response

    with mock.patch.object(controller.http_client, 'fetch', fetch):
        yield controller.refresh()

    assert account.access_token == expect.get('access_token')
    assert account.refresh_token == expect.get('refresh_token')
    assert (expect.get('token_expiration') - account.token_expiration) < (
        datetime.timedelta(seconds=1))


@pytest.mark.gen_test
def test_auth_controller_should_fetch_with_refresh(db, current_user):
    controller = CloudplayerController(db, current_user)
    account = controller.account
    account.token_expiration = datetime.datetime(2015, 7, 14, 12, 30)

    @tornado.gen.coroutine
    def fetch(path, method=None, **kw):
        response = mock.Mock()
        response.error = None
        if method:
            response.body = json.dumps({'access_token': 'access-token'})
        else:
            response.body = json.dumps({'path': path})
        return response

    with mock.patch.object(controller.http_client, 'fetch', fetch):
        response = yield controller.fetch('/path')

    fetched = tornado.escape.json_decode(response.body)
    assert fetched.get('path') == (
        'cp://base-api'
        '/path?token-param=access-token&client-key=cp-api-key')


@pytest.mark.gen_test
def test_auth_controller_should_fetch_anonymously(db):
    controller = CloudplayerController(db, {})

    @tornado.gen.coroutine
    def fetch(path, **kw):
        response = mock.Mock()
        response.error = None
        response.body = json.dumps({'path': path})
        return response

    with mock.patch.object(controller.http_client, 'fetch', fetch):
        response = yield controller.fetch('/path')

    fetched = tornado.escape.json_decode(response.body)
    assert fetched.get('path') == (
        'cp://base-api/path?client-key=cp-api-key')
