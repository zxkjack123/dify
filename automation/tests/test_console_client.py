import unittest
from unittest.mock import MagicMock, patch
from automation.clients.dify_console_client import (
    DifyConsoleClient,
    AuthenticationError,
)


class TestDifyConsoleClient(unittest.TestCase):

    def setUp(self):
        # Mock config
        self.config_patcher = patch(
            'automation.clients.dify_console_client.Config'
        )
        self.mock_config_cls = self.config_patcher.start()
        self.mock_config_instance = self.mock_config_cls.return_value
        self.mock_config_instance.DIFY_CONSOLE_URL = "http://mock-dify"
        self.mock_config_instance.DIFY_EMAIL = "test@example.com"
        self.mock_config_instance.DIFY_PASSWORD = "password"


    def tearDown(self):
        self.config_patcher.stop()

    @patch('requests.Session')
    def test_login_success(self, mock_session_cls):
        mock_session = mock_session_cls.return_value
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_session.post.return_value = mock_response

        client = DifyConsoleClient()
        client.login()

        self.assertTrue(client.logged_in)
        mock_session.post.assert_called_once()

    @patch('requests.Session')
    def test_login_failure(self, mock_session_cls):
        mock_session = mock_session_cls.return_value
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.raise_for_status.side_effect = Exception("401")
        
        # We need to simulate the HTTPError properly if we want to catch it
        # in the client. But for simplicity, let's just check if it raises
        # exception when post fails.

        # The client code catches requests.exceptions.HTTPError
        
        import requests
        error = requests.exceptions.HTTPError(response=mock_response)
        mock_session.post.side_effect = error

        client = DifyConsoleClient()
        with self.assertRaises(AuthenticationError):
            client.login()

    @patch('requests.Session')
    def test_get_apps_auto_login(self, mock_session_cls):
        mock_session = mock_session_cls.return_value
        
        # Setup login response
        login_response = MagicMock()
        login_response.status_code = 200
        
        # Setup apps response
        apps_response = MagicMock()
        apps_response.status_code = 200
        apps_response.json.return_value = {"data": []}

        # Configure side effects
        # First call is login (inside _request), second is get apps
        # Wait, _request calls login() which calls session.post
        # Then _request calls session.request
        
        mock_session.post.return_value = login_response
        mock_session.request.return_value = apps_response

        client = DifyConsoleClient()
        apps = client.get_apps()

        self.assertEqual(apps, {"data": []})
        self.assertTrue(client.logged_in)
        mock_session.post.assert_called_once()  # Login called
        mock_session.request.assert_called_once()  # API call


if __name__ == '__main__':
    unittest.main()

