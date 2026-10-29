# Monitoring

## Current stack

- [Splunk logging box on security-tooling-01](../infrastructure_tooling/monitoring_stack/splunk/README.md) (Splunk, Cribl, detections, dashboards)
- [OTel host agent](../infrastructure_tooling/monitoring_stack/splunk/otel-agent/README.md)
- [OpenTelemetry Collector on k3s-01](../infrastructure_tooling/otel_collector_k3s/README.md)
- [k3s API audit log](../infrastructure_tooling/k3s_audit/README.md)
- [Falco](../infrastructure_tooling/monitoring_stack/falco/README.md)
- [OpenCanary honeypot](../infrastructure_tooling/opencanary/README.md)
- [ToolHive MCP gateway](../infrastructure_tooling/toolhive_mcp/README.md)
- [Homelab agents](../infrastructure_tooling/homelab_agents/README.md)

## Monitoring Stack Setup - Elastic & Cribl
> **Update:** the homelab logging box is now Splunk Enterprise on a developer license, running on `security-tooling-01`. See [`infrastructure_tooling/monitoring_stack/splunk/README.md`](../infrastructure_tooling/monitoring_stack/splunk/README.md). The Elastic and Cribl write-up below is kept for reference.

- Using Docker Compose on a VM within Proxmox, I have configured a basic monitoring stack leveraging Elastic and Cribl for my logging infastructure
    - In order to avoid requring a license (e.g. Splunk Developer license) I have chosen Elastic as my logging solution along with Cribl to allow for easy data ingestion
- In this section I will breakdown setting up both Elastic and Cribl utilizing running both Elastic and Cribl on a single VM with Docker Compose

### Prerequisites
- Virtual Machine - *Recommended*
  - In my case, I am running a Ubuntu Server on a VM within my Proxmox homelab
    - You can run this all locally but for my Homelab, I want this running uninterrupted 
- Services that are required to run this
  - [Docker](https://docs.docker.com/engine/install/)
  - [Docker Compose](https://docs.docker.com/compose/install/linux/)
  - Git
  - Clone down this repository

### In Scope
- Initial stand up and configuration of Elastic and Cribl
#### Elastic
- Creating a `dev_logs` index via Elastic Dev Tools
- Creating a Cribl logging user via Elastic Dev Tools

#### Cribl
- Creating a Data Source for Splunk HEC
- Creating a Data Destionation for Elastic
- Creating a Processing Pipeline to override the default Elastic Destination
- Creating a Data Route utlizing the Splunk HEC Source, Processing Pipeline, and Elastic Destination

### Out of Scope
- Advanced Elastic index management (e.g. Sharding, correct field mappings (outside of timestamp), etc.)
- Configuring streaming of applications / infrastructure (e.g. I am only walking through initital setup of a test Cribl pipeline)
- Configuring valid TLS and domain names
- Deploying this in an automated method or via Kubernetes


### Elastic
- Following the [Getting started with the Elastic Stack and Docker Compose: Part 1](https://www.elastic.co/blog/getting-started-with-the-elastic-stack-and-docker-compose) guide, I have deployed the Elastic stack with Docker compose.
- The `docker-compose.yml` file can be found at [elastic-stack-docker-part-one](https://github.com/elkninja/elastic-stack-docker-part-one) or in the [monitoring_stack/elastic](../infrastructure_tooling/monitoring_stack/elastic/docker-compose.yml) directory in this repo.


### Cribl
- Following the [Docker Deployment](https://docs.cribl.io/stream/deploy-docker/) guide, I have deployed Cribl with a Leader and worker node with Docker compose.
- The `docker-compose.yml` file can be found in the link above or in the [monitoring_stack/cribl](../infrastructure_tooling/monitoring_stack/cribl/docker-compose.yml) directory in this repo.
> NOTE: I have modifed my `docker-compose.yml` file to expose Port 8088 in order to allow for us to ingest logs via the [Splunk HEC Source](https://docs.cribl.io/stream/sources-splunk-hec/)


#### Setup
1. To begin we will cofigure Elastic, change into the directory where you have your `docker-compose.yml` (e.g. the [monitoring_stack/elastic](../infrastructure_tooling/monitoring_stack/elastic/) directory) file for elastic and run the docker compose up command. Run the two commands to move the [monitoring_stack/elastic/.env-example](../infrastructure_tooling/monitoring_stack/elastic/.env-example) file to `.env` and then start up the Docker compose stack.
> NOTE: You just have all of the additional configuration (e..g. [monitoring_stack/elastic/filebeat.yml](../infrastructure_tooling/monitoring_stack/elastic/filebeat.yml), [monitoring_stack/elastic/logstash.conf](../infrastructure_tooling/monitoring_stack/elastic/logstash.conf), [monitoring_stack/elastic/metricbeat.yml](../infrastructure_tooling/monitoring_stack/elastic/metricbeat.yml), and [monitoring_stack/elastic/.env-example](../infrastructure_tooling/monitoring_stack/elastic/.env-example) file in the same directory as your `docker-compse.yml` file.

```bash
mv .env-exmaple .env
docker compose up -d
```
2. Once up, in a browser go to `http://<vm_instnace_ip>:5601` and login with the credentials you have in the `.env` file.
> NOTE: the default credentials that in this example are `elastic:changeme`. You can update this to whatever password you want in the `.env` file before starting the compose template.

3. Validate that you have logged into Elastic and click 'Explore on my own' to continue to the main interface.

4. On the left hand side, if it is not expanded, click the hamburger menu (e.g. three stacked bars) to expand the menu and then scroll down to Management > Click Dev Tools

5. In your Dev Tools Console, paste in the line below to create the `dev_logs` index and the `failover` index. 
> NOTE: Once you paste the below code into your Dev Tools console, click on each code area and hit the play button on the right hand side of your Dev Tools console. You will have to do this for each command you place into the Dev Tools console.
```
PUT /dev_logs
{
  "settings": {
    "number_of_shards": 1,
    "number_of_replicas": 0
  },
  "mappings": {
    "properties": {
      "@timestamp": { "type": "date" }
    }
  }
}

PUT /failover
{
  "settings": {
    "number_of_shards": 1,
    "number_of_replicas": 0
  },
  "mappings": {
    "properties": {
      "@timestamp": { "type": "date" }
    }
  }
}
```
> NOTE: The `failover` index is used as our catch all index for data we will be sending over via Cribl.

6. To verify the index has been created you can run the below command and you should see a a reponse with the informationf or the index.
```
GET /dev_logs/
GET /failover/
```

7. Next, we will create a custom Cribl role in Elastic to allow for us to write data to our `dev_logs` and `failover` indexes
```
POST /_security/role/cribl_writer_full
{
  "cluster": [ "monitor" ],
  "indices": [
    {
      "names": [ "dev_logs", "failover" ],
      "privileges": [ "write", "create", "index" ]
    }
  ]
}
```
> NOTE: You can and as my indexes to this as you need along with utilizing wildcard (e.g. "dev_logs*") if you want to allow for the user to write to any index prefixed (e.g. `dev_logs_blah`)

8. Create a Cribl user to allow for Cribl to write to the `dev_logs` index along with the custom role we created above.
```
POST /_security/user/cribl_writer
{
  "password" : "criblpass",
  "roles" : [ "cribl_writer_full" ],
  "full_name" : "Cribl Stream Log Writer User"
}
```

9. Verify that the user has been created and has the `cribl_writer_full` role that we created
```
GET /_security/user/cribl_writer
```

> At this point, the initial Elasitc componenets should be ready to go and we are onto setting up Cribl to ingest data.

10. To begin configuring Cribl, change into the directory where you have your `docker-compose.yml` file for Cribl and run
```bash
docker compose up -d
```

11. Once up, in a browser go to `http://<vm_instnace_ip>:19000` and login with the default credentials..
> NOTE: the default credentials that in this example are `admin:admin`. You can update this to whatever password you want at this time.

12. Fill out whatever information in the screen and regitster
>NOTE: This does not matter at all just put in an email that passes the form validate (e.g. a.b@c.com)

13. Enter in a new password that passes the password validate.

14. On the top left of your screen, click Products > Stream. You should now be at the Cribl Stream main page and see 1 worker in the `default` group

15. Near the top lieft, click on `default` to enter into your Cribl Stream interface

16. Click Data > Sources > Splunk HEC. On the new page, click on the grayed out line labeled `in_splunk_hec` to enter into your Splunk HEC source configuration page

17. At the top right of the window, toggle `Enabled` to true (it should now show as blue)

18. In the same window, click on Auth Tokens > Add Token > On the right hand side click `Generate`, add a Description such as `Sample HEC Logs`, and then hit `Save`.

19. At the top right, click Commit and enter a message such as `Enabling Splunk HEC source.`. Then on the right hand side click `Commit & Deploy`
> NOTE: A common theme with Cribl will be to always validate you have done the `Commit & Deploy` as that is what will make your changes 'active'.

20. At the top left, click Data > Destinations > Elasticsearch. On the top right, click `Add Destination` and then fill out the New Destination pane with infomration such as:
  - *Output ID:* elastic_dev
  - *Description:* Elastic Destination for necessary logs with a Default index set to `failover`
  - *Bulk API URLs:* https://<vm_instance_ip>:9200
  - *Index or data stream:* failover
  - *Enable Authentication:* Toggle to Enabled (e.g. it should show blue)
    - *Authenitcation method:* Manual
    - *Username:* cribl_writer
    - *Password:* criblpass

21. Under `Advanced Settings`, toggle Validate server certs to off (e.g. make it gray).

22. Once you have finished filling that out. hit `Save`. And then at the top right click, `Commit`. Add a message such as `Enabling elastic dev destination` and then at the right hit `Commit & Deploy`

23. Click back on the new destination that was created labeled `elastic_dev`. On the top navigation bar of your destination window, click Test > Run Test. This will send sample events to the failover index. If successful, you will see on the bottom left.
> NOTE: If you get an error here, confirm that you have committed and deployed your new Destination. 

24. We will now capture some sample data to use in our Processing Pipeline. Click Data > Sources > Splunk HEC. Now click on the Splunk HEC source labeled `in_splunk_hec`. Go to Auth Tokens > Drop down the `Smaple HEC Logs` token and on the right hand side click on the crossed out eye to revael your HEC token and copy that.

25. In order to send sample logs, we will use the `sample_dev_logs.py` script to send data. In order to run this, copy the `.env-example` file to `.env` and update this to inclue the URL of your VM Server IP and the HEC token from the previous step.
> NOTE: This is assuming that you have copied down all of the contents fo the [monitoring_stack/cribl](../infrastructure_tooling/monitoring_stack/cribl) directory and are running this from your local PC that has access to your VM.

26. After saving the `.env` file, run
```bash
docker build -t sample_dev_logs .
```

27. Begin running the container
```bash
docker run --rm sample_dev_logs
```
> NOTE: You should now see logs being generated such as `2025-05-26T03:19:24+0000 INFO Sent batch of 100 events → Status Code: 200` in your console

28. Go back into your Cribl browser window and on the HEC input, navigate up to `Live Data` > Click Capture... >Change Capture time to 60 and click `Start`.

29. After 30 to 60 seconds, you should see events populate on your feed. Once they are there, on the bottom right, click `Save as Sample File` with the information below:
  - *File name:* sample_dev_logs.txt
  - *Description:* Sample Dev Logs
  - *Expiration:* 0

30. Once you have filled that information out, click `Save`.

31. Exit out of the Splunk HEC input pane and at the top right click `Commit` and enter a message such as `Adding Sample Dev logs for processing pipeline` and click `Commit & Deploy`.

32. Now we will create our processing pipeline by navigating to > Processing > Pipelines > Add Pipeline > Add Pipeline > Give it the ID of `dev_logs_processing` and click `Save`.

33. We will have to add two function to clean up the events and enable us to dynamically route the data to our `dev_logs` index. First, on the right hand side double click the sample events that we have captured from above (e.g. `sample_dev_logs.txt`) in order to load them in.

34. Click Add Function > Type in `Parser` and then click Parser. Once it has loaded in, update the `Type` to `JSON Object` and click `Save`.
> NOTE: You should now see on the right hand side green highlighted fields added in which represent the events that will be send over to Cribl with our processing pipeline.

35. We will now add another function to remove the `_raw` field and update the metadata fields to route our events to the `dev_logs` index. Click Add Function > Type in `Eval` anbd click Eval. Configure the new Function as below:
  - Click Add Field
    - *Name:* __index
    - *Value Expression:* 'dev_logs'
      > NOTE: You must add the single quotes as Cribl requires this to be in a javascript string format
  - Remove fields, add each of these fields individually and hit tab. You should see it turn into a grab box for each of the fields accordingly.
    - index
    - _raw

36. Click `Save` and validate on the right hand side in our OUT pane that the _raw field and index field are highlighted red and crossed out indicating they will be removed
> NOTE: if you want to see the internal fields (e.g. __index), on the top right of your samples pane click the gear icon and click the `Show Internal Fields` for it to toggle blue. This will now so you the metadata fields (e.g. fields appended by `__`)

37. On the top right click Commit and enter in a message such as `Adding in dev logs processing pipeline` and click `Commit & Deploy`

38. We are now ready to build our route. On the top left, click Routing > Data Routes > Add Route. Fill out the route with the information below:
  - *Route name:* dev_logs
  - *Filter:* __inputId=='splunk_hec:in_splunk_hec' && sourcetype=='sample_dev_logs'
  - *Pipeline:* dev_logs_processing
  - *Destination:* elastic:elastic_dev

39. Click `Save` and move your new Route up to the first position (e.g. on the left hand side the stacked dots next to the numbers will allow you to click and drag) and on the top right, click `Commit` and enter a message such as `Adding dev_logs route` and click `Commit & Deploy` 

40. Once deployed, rerun the sample dev logs container on your PC
```bash
docker run --rm sample_dev_logs
```

41. Events should now be flowing through the Cribl pipeline and outputting to Elastic. In order to validate, go back to your Elastic Dev tools console and run:
```bash
GET /dev_logs/_count
```
> NOTE: On the right hand side, you should see a number greater than `0` for the count value.

42. We can now go search the logs. On the left hand side, if it is not expanded, click the hamburger menu (e.g. three stacked bars) to expand the menu and under Analytics, Click Discover > Create data view with the information below:
  - *Name:* dev_logs
  - *Index pattern:* dev_logs
  - *Timestamp field:* @timestamp

43. Click `Save data view to Kibana`

44. You should now see your sample data events populated and are available for search. If you do not see any events, you can try to click the Calender icon on the top right and select `Last 24 hours` to search back for data you may have ingested earlier.

- At this point your Elastic stack and Cribl pipelines should be ready for you to create additional dashboards, searches, and ingest additional data as necessary.

> NOTE: As stated, this guide is the initial stand up elements and does not cover more advanced use cases at this point.

##### Cribl Links
- [Using ACLs to allow Cribl Edge to Read Files](https://docs.cribl.io/edge/usecase-edge-acls/)
- [Cribl Edge to Cribl Stream](https://docs.cribl.io/edge/usecase-edge-stream/)
- [Cribl Stream Persistent Queues](https://docs.cribl.io/stream/persistent-queues/)
- [Cribl Edge Persistent Queues](https://docs.cribl.io/edge/persistent-queues/)

##### Elastic Links
