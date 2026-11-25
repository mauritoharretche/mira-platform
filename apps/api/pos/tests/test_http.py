from uuid import uuid4

from django.contrib.auth.models import Permission
from django.test import TestCase
from identity.tests.test_http_authentication import operator_client, sign_in
from pos.models import Sale
from .test_application import PosFixture
from .fixtures import PASSWORD

PREFIX='/api/v1/operator/pos'


class PosHttpTests(PosFixture,TestCase):
    def setUp(self):
        super().setUp();self.client=operator_client()
        response=sign_in(self.client,username=self.user.username,password=PASSWORD)
        self.csrf=response.json()['csrf_token']

    def post(self,path,data,csrf=True):
        return self.client.post(PREFIX+path,data,content_type='application/json',secure=True,**({'HTTP_X_CSRFTOKEN':self.csrf} if csrf else {}))

    def test_real_session_quote_confirm_lookup_history_and_csrf(self):
        payload={'lines':[{'variant_id':str(self.each.pk),'quantity':'1','unit':'EACH'}]}
        self.assertEqual(self.post('/quotes',payload,False).status_code,403)
        response=self.post('/quotes',payload);self.assertEqual(response.status_code,200,response.json())
        q=response.json()['quote'];op=str(uuid4())
        result=self.post('/sales',{'operation_id':op,'quote_id':q['id']});self.assertEqual(result.status_code,200,result.json())
        self.assertEqual(result.json()['sale']['settlement'],'UNSETTLED')
        self.assertEqual(self.client.get(PREFIX+'/operations/'+op,secure=True).json(),result.json())
        self.assertEqual(self.client.get(PREFIX+'/sales',secure=True).json()['items'][0]['id'],result.json()['sale']['id'])
        self.assertEqual(self.client.get(PREFIX+'/sales/'+result.json()['sale']['id'],secure=True).status_code,200)
        self.reconcile()

    def test_authorize_before_parsing_or_revealing_existence(self):
        self.user.user_permissions.clear()
        for path in ['/quotes/invalid','/sales/invalid','/operations/invalid','/products','/sales']:
            self.assertEqual(self.client.get(PREFIX+path,secure=True).status_code,403)
        self.assertEqual(self.post('/quotes',{'invalid':True}).status_code,403)
        self.assertEqual(self.post('/sales',{'invalid':True}).status_code,403)

    def test_strict_strings_duplicate_queries_unknown_fields_and_overprecision(self):
        for value in [True,1,1.5,'1.1','0','-1','1e2']:
            r=self.post('/quotes',{'lines':[{'variant_id':str(self.each.pk),'quantity':value,'unit':'EACH'}]})
            self.assertEqual(r.status_code,400,(value,r.json()))
        self.assertEqual(self.post('/sales',{'operation_id':str(uuid4()),'quote_id':str(uuid4()),'total':'1.00'}).status_code,400)
        self.assertEqual(self.client.get(PREFIX+'/products?search=a&search=b',secure=True).status_code,400)
        self.assertFalse(Sale.objects.exists())

    def test_session_capabilities_and_no_inventory_privilege_escalation(self):
        response=self.client.get('/api/v1/operator/auth/session',secure=True).json()
        self.assertIn('pos.confirm_sale',response['capabilities']);self.assertNotIn('inventory.consume_reservation',response['capabilities'])
        self.user.user_permissions.remove(Permission.objects.get(content_type__app_label='pos',codename='confirm_sale'))
        self.assertNotIn('pos.confirm_sale',self.client.get('/api/v1/operator/auth/session',secure=True).json()['capabilities'])
        self.assertEqual(self.post('/sales',{'operation_id':str(uuid4()),'quote_id':str(uuid4())}).status_code,403)
