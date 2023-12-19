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

Data type (_For example: observation, forecast_):

Other metadata (_For example: provider, observation_type, model_): 

Timeline (_When does this issue need to be completed_):

## Date range

Start date: 

End date:

## Source host and data store

Source host: 

Source data store:

## Target data store type (select one)

- [ ] archive
- [ ] experiments
- [ ] gfsensemble
- [ ] geosensemble
- [ ] other 
    - Please describe: 

## Data ingest processing checklist for the R2D2 Administrator

Data has been ingested into the target data store type on the following data hubs:

- [ ] AWS ParallelCluster R&D
- [ ] Casper/Derecho (NCAR-Wyoming Supercomputing Center)
- [ ] Discover (NASA Center for Climate Simulation)
- [ ] Orion/Hercules (Mississippi State University High Performance Computing)
- [ ] S3 (jcsda-noaa-aws-us-east-1)
- [ ] S3 (jcsda-usaf-aws-us-east-2)
- [ ] S4 (Space Science and Engineering Center)

## Associated pull request in [r2d2/scripts](https://github.com/JCSDA-internal/r2d2/tree/develop/scripts) (if creating new ingestion scripts)

*(This instruction will change once we create the r2d2-ingest repository.)*
