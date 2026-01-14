import pytest
import tempfile
import os
from pathlib import Path
from unittest.mock import patch, mock_open

# Import the app
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from sincor_app import app, clean_phone, send_email

@pytest.fixture
def client():
    """Create a test client for the Flask app."""
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)

class TestCleanPhone:
    """Test phone number cleaning functionality."""
    
    def test_clean_phone_basic(self):
        assert clean_phone("1234567890") == "+11234567890"
        assert clean_phone("(123) 456-7890") == "+11234567890"
        assert clean_phone("123.456.7890") == "+11234567890"
    
    def test_clean_phone_with_plus(self):
        assert clean_phone("+11234567890") == "+11234567890"
        assert clean_phone("+1-123-456-7890") == "+11234567890"
    
    def test_clean_phone_empty(self):
        assert clean_phone("") == ""
        assert clean_phone(None) == ""
        assert clean_phone("   ") == ""

class TestSendEmail:
    """Test email sending functionality."""
    
    @patch.dict(os.environ, {'EMAIL_TO': 'test@example.com'})
    @patch('sincor_app.LOGFILE', Path('/tmp/test.log'))
    @patch('sincor_app.OUT', Path('/tmp/outputs'))
    def test_send_email_draft_mode(self):
        """Test email creation in draft mode (no SMTP config)."""
        with patch('sincor_app.Path.mkdir'), \
             patch('sincor_app.Path.write_bytes') as mock_write, \
             patch('sincor_app.log'):
            
            result = send_email("Test Subject", "Test Body")
            
            assert result["sent"] is False
            assert result["method"] == "draft"
            assert "file" in result
            mock_write.assert_called_once()
    
    @patch.dict(os.environ, {'EMAIL_TO': ''})
    def test_send_email_no_recipients(self):
        """Test email when no recipients configured."""
        with patch('sincor_app.log'):
            result = send_email("Test", "Body")
            assert result["method"] == "error"
            assert "EMAIL_TO not configured" in result["error"]

class TestWebRoutes:
    """Test Flask web routes."""
    
    def test_home_page(self, client):
        """Test the home page loads."""
        response = client.get('/')
        assert response.status_code == 200
        assert b'SINCOR Lead Engine' in response.data
    
    def test_lead_form(self, client):
        """Test the lead form page."""
        response = client.get('/lead')
        assert response.status_code == 200
        assert b'Book a Detail' in response.data
    
    def test_health_check(self, client):
        """Test the health check endpoint."""
        response = client.get('/health')
        assert response.status_code == 200
        data = response.get_json()
        assert data['ok'] is True
    
    @patch('sincor_app.save_lead')
    @patch('sincor_app.send_email')
    def test_lead_submission_valid(self, mock_email, mock_save, client):
        """Test valid lead submission."""
        mock_email.return_value = {"sent": False, "method": "draft"}
        
        response = client.post('/lead', data={
            'name': 'John Doe',
            'phone': '1234567890',
            'service': 'Full Detail',
            'notes': 'Test booking'
        })
        
        assert response.status_code == 200
        assert b'Request received' in response.data
        mock_save.assert_called_once()
        mock_email.assert_called_once()
    
    def test_lead_submission_missing_data(self, client):
        """Test lead submission with missing required fields."""
        response = client.post('/lead', data={
            'name': '',  # Missing name
            'phone': '1234567890',
            'service': 'Full Detail'
        })
        
        assert response.status_code == 400
        assert b'Missing name/phone' in response.data
    
    @patch('sincor_app.LOGFILE')
    def test_logs_endpoint_no_file(self, mock_logfile, client):
        """Test logs endpoint when log file doesn't exist."""
        mock_logfile.exists.return_value = False
        mock_logfile.__str__ = lambda x: '/tmp/test.log'
        
        response = client.get('/logs')
        assert response.status_code == 200
        data = response.get_json()
        assert data['tail'] == []
    
    @patch('sincor_app.OUT')
    @patch('os.walk')
    def test_outputs_endpoint(self, mock_walk, mock_out, client):
        """Test outputs endpoint."""
        mock_walk.return_value = [
            ('/tmp/outputs', [], ['test.txt'])
        ]
        mock_out.__truediv__ = lambda x, y: Path('/tmp/outputs')
        
        with patch('sincor_app.ROOT', Path('/tmp')):
            response = client.get('/outputs/')
            assert response.status_code == 200