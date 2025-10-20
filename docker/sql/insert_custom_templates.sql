\set ON_ERROR_STOP on
\pset pager off
\t on

-- Parent-child template
DELETE FROM pipeline_customized_templates 
WHERE tenant_id=(SELECT id FROM tenants LIMIT 1)
  AND name='file-parentchild-external-embedding-hybrid'
  AND language='en-US';

INSERT INTO pipeline_customized_templates 
(tenant_id,name,description,chunk_structure,icon,position,yaml_content,install_count,language,created_by)
SELECT 
  t.id,
  'file-parentchild-external-embedding-hybrid',
  'Parent-child chunking for files using external OpenAI-compatible embeddings (sudon-sd-bge-m3) and hybrid retrieval.',
  'hierarchical_model',
  '{"icon_type":"emoji","icon":"📙","icon_background":"#FFF4ED"}'::json,
  COALESCE((SELECT MAX(position) FROM pipeline_customized_templates WHERE tenant_id=t.id),0) + 1,
  pg_read_file('/tmp/file1.rag.yml'),
  0,
  'en-US',
  (SELECT id FROM accounts LIMIT 1)
FROM tenants t
LIMIT 1;

-- General template
DELETE FROM pipeline_customized_templates 
WHERE tenant_id=(SELECT id FROM tenants LIMIT 1)
  AND name='file-general-external-embedding-hybrid'
  AND language='en-US';

INSERT INTO pipeline_customized_templates 
(tenant_id,name,description,chunk_structure,icon,position,yaml_content,install_count,language,created_by)
SELECT 
  t.id,
  'file-general-external-embedding-hybrid',
  'General chunking for files using external OpenAI-compatible embeddings (sudon-sd-bge-m3) and hybrid retrieval.',
  'text_model',
  '{"icon_type":"emoji","icon":"📙","icon_background":"#FFF4ED"}'::json,
  COALESCE((SELECT MAX(position) FROM pipeline_customized_templates WHERE tenant_id=t.id),0) + 1,
  pg_read_file('/tmp/file2.rag.yml'),
  0,
  'en-US',
  (SELECT id FROM accounts LIMIT 1)
FROM tenants t
LIMIT 1;
