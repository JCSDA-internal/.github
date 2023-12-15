---
name: Data ingest request
about: Use this template for ingesting data into r2d2
title: "[Ingest]"
labels: ''
assignees: ''

---

## Ingest description

*(Instructions: Please provide values for the following entries.)*

Description: 

Data type (_For example, observation, forecast_):

Other metadata (_For example, provider, observation type, model_): 

Timeline (_When does this issue need to be completed_):

## Date range

Start date: 

End date:

## Source host and directory/bucket

Source host: 

Data store directory path or S3 bucket path (_with wildcards if needed_):

## Target data store type (select one)

- [ ] archive
- [ ] experiments
- [ ] gfs ensemble
- [ ] geos ensemble
- [ ] other 
    - Please describe: 

## Data store checklist

Data copied into R2D2 on:

- [ ] AWS ParallelCluster R&D
- [ ] Casper/Derecho
- [ ] Discover
- [ ] Orion/Hercules
- [ ] S3 (us-east-1)
- [ ] S3 (us-east-2)
- [ ] S4

## Associated pull request in [r2d2/scripts](https://github.com/JCSDA-internal/r2d2/tree/develop/scripts) (if creating new ingestion scripts)

*(This instruction will change once we create the r2d2-ingest repository.)*
