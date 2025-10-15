import boto3
import os
from datetime import datetime, timedelta
from typing import Dict, Optional
import hmac
import hashlib
import base64

class MarketplaceStorage:
    def __init__(self):
        self.s3_client = boto3.client('s3') if os.getenv('AWS_ACCESS_KEY_ID') else None
        self.bucket = os.getenv('MARKETPLACE_BUCKET', 'sincor-marketplace')
        self.signing_secret = os.getenv('MARKETPLACE_SIGNING_SECRET', 'dev-secret')
    
    def generate_signed_url(self, key: str, expires_in: int = 3600) -> str:
        """Generate a signed download URL for purchased items"""
        if self.s3_client:
            # AWS S3 signed URL
            return self.s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': self.bucket, 'Key': key},
                ExpiresIn=expires_in
            )
        else:
            # Local development signed URL
            expiry = int((datetime.now() + timedelta(seconds=expires_in)).timestamp())
            signature = self._sign_url(key, expiry)
            return f"/downloads/{key}?expires={expiry}&signature={signature}"
    
    def _sign_url(self, key: str, expiry: int) -> str:
        """Sign a URL for secure download"""
        data = f"{key}:{expiry}"
        signature = hmac.new(
            self.signing_secret.encode(),
            data.encode(),
            hashlib.sha256
        ).digest()
        return base64.urlsafe_b64encode(signature).decode().rstrip('=')
    
    def verify_signature(self, key: str, expiry: int, signature: str) -> bool:
        """Verify a signed download URL"""
        try:
            expected = self._sign_url(key, expiry)
            return hmac.compare_digest(signature, expected)
        except:
            return False
    
    async def store_template(self, content: bytes, filename: str, metadata: Dict = None) -> str:
        """Store a template file and return its key"""
        key = f"templates/{datetime.now().strftime('%Y/%m')}/{filename}"
        
        if self.s3_client:
            # Upload to S3
            extra_args = {}
            if metadata:
                extra_args['Metadata'] = metadata
                
            self.s3_client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=content,
                **extra_args
            )
        else:
            # Store locally for development
            local_path = f"/tmp/marketplace/{key}"
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            with open(local_path, 'wb') as f:
                f.write(content)
        
        return key
    
    async def get_template_info(self, key: str) -> Optional[Dict]:
        """Get template file info"""
        if self.s3_client:
            try:
                response = self.s3_client.head_object(Bucket=self.bucket, Key=key)
                return {
                    'size': response.get('ContentLength'),
                    'last_modified': response.get('LastModified'),
                    'content_type': response.get('ContentType'),
                    'metadata': response.get('Metadata', {})
                }
            except:
                return None
        else:
            # Local file info
            local_path = f"/tmp/marketplace/{key}"
            if os.path.exists(local_path):
                stat = os.stat(local_path)
                return {
                    'size': stat.st_size,
                    'last_modified': datetime.fromtimestamp(stat.st_mtime),
                    'content_type': 'application/octet-stream'
                }
            return None