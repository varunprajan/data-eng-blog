# Retry-driven development

## Introduction

I usually advise against writing your own ETL jobs. Commercial products such as Stitch and Fivetran integrate with hundreds of data sources and dozens of data destinations; they make ETL maintainable and easy. But, whether for reasons of cost or functionality, you might be forced to write your own ETL. How do you do that in a way that avoids its common pitfalls? (To [quote](https://medium.com/@maximebeauchemin/functional-data-engineering-a-modern-paradigm-for-batch-data-processing-2327ec32c42a) Maxime Beauchemin, creator of Apache Superset and Apache Airflow: "Batch data processing — historically known as ETL — is extremely challenging. It’s time-consuming, brittle, and often unrewarding. Not only that, it’s hard to operate, evolve, and troubleshoot.")

Data engineers should obviously borrow best practices from other disciplines of software engineering: writing clear documentation, developing robust unit and integration tests, soliciting PR reviews, etc. One under-appreciated method that has special relevance to data/analytics engineering is what I'll call "retry-driven development". In short, *write your code in such a way that it can be retried safely and correctly.* In other words, accept that bugs can happen that will not be covered by your test suite, but be able to recover from them effectively.

To be clear, I am not claiming credit for this idea. In fact, much of this post is inspired by Beauchemin's excellent [essay](https://medium.com/@maximebeauchemin/functional-data-engineering-a-modern-paradigm-for-batch-data-processing-2327ec32c42a) "Functional Data Engineering: A Modern Paradigm for Batch Data Processing" (and he himself acknowledges that mature companies have been employing these ideas for years, perhaps without formalizing them in the way he has). My hope is instead to work through a concrete case study that might help illuminate some of the slightly abstract concepts he discussed.

## A case study

Suppose we work for a retail company that has historically operated only in the United States, but  now seeks to expand its operations worldwide. We can imagine an `order_items` table that used to look like this:

| order_item_id | order_id | item_name | amount_usd | ordered_at |
| :-----------: | :------: | :-------: | :--------: | :--------: |
| 1 | 1 | Breezy Dress | 53.49 | 2020-12-01 02:00:01 |
| 2 | 2 | V-neck Shirt | 18.99 | 2020-12-03 04:00:01 |
| 3 | 2 | Muscle Tank  | 42.69 | 2020-12-03 04:00:01 |

But will be migrated to look like this (note the additional item in a non-USD currency):

| order_item_id | order_id | item_name | amount | currency_code | ordered_at |
| :-----------: | :------: | :-------: | :----: | :-----------: | :--------: |
| 1 | 1 | Breezy Dress | 53.49 | USD | 2020-12-01 02:00:01 |
| 2 | 2 | V-neck Shirt | 18.99 | USD | 2020-12-03 04:00:01 |
| 3 | 2 | Muscle Tank | 42.69 | USD | 2020-12-03 04:00:01 |
| 4 | 3 | Red Sari | 1800.00 | INR | 2020-12-05 23:49:04 |

We would like to be able to continue reporting our daily gross revenue (in USD) in our company "key metrics email". To do this, we will collect data on a daily basis for worldwide exchange rates in terms of USD. Suppose this table is called `historical_exchange_rates`, with three columns, `date` ,`currency_code`, and `exchange_rate_usd`, like so:

| date | currency_code | exchange_rate_usd |
| :--: | :-----------: | :---------------: |
| 2020-12-01 | USD | 1.00 |
| 2020-12-01 | INR | 0.0136 |
| 2020-12-02 | USD | 1.00 |
| ... | ... | ... |

our revenue query for the report would be:

```sql
SELECT
    order_items.ordered_at::DATE,
    -- ignore revenue for which a matching code/date cannot be found
    SUM(order_items.amount * COALESCE(historical_exchange_rates.exchange_rate_usd, 0))
FROM order_items
LEFT JOIN historical_exchange_rates ON order_items.ordered_at::DATE = historical_exchange_rates.date
                                    AND order_items.currency_code = historical_exchange_rates.currency_code
WHERE
    -- only report on data from the last 60 days
    ordered_at > CURRENT_DATE - INTERVAL '60 DAY'
GROUP BY 1
```

which is actually fairly straightforward. (Whether this is good enough for financial reporting is a separate issue that is out of scope for this post.)

## ETL, first attempt

Our first attempt at an ETL script for this `historical_exchange_rates` table might look something like this. For brevity, I've omitted most of the code, but it is available on Github [TODO]).

```python
import logging
logger = logging.getLogger(__name__)

BASE_CURRENCY = "USD"
DB_CONN = RedshiftConnection(...)
EXCHANGE_RATES_URL = "https://openexchangerates.org/api/"
EMAIL_RECIPIENTS = ["alex@retailstartup.com", "varun@retailstartup.com"]
TABLE_NAME = "historical_exchange_rates"

def retrieve_exchange_rates(base_currency: str, url: str) -> Dict:
    ...

def transform_data(raw_data: Dict) -> pd.DataFrame:
    ...

def append_data(frame: pd.DataFrame, db_conn: 'DatabaseConnection'):
    ...

def main():
    # retrieve raw data for today by making a request to the API
    json_data = retrieve_exchange_rates(
        base_currency=BASE_CURRENCY,
        url=EXCHANGE_RATES_URL,
    )

    # transform data into pandas dataframe
    frame = transform_data(raw_data=json_data)

    # insert (append) it into our data warehouse table
    append_data(frame, db_conn=DB_CONN, table=TABLE_NAME)

    # email recipients that job finished
    email_notification(
        recipients=EMAIL_RECIPIENTS,
        contents="Exchange rate job succeeded!",
    )

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.info(f"Something bad happened, exception info: {e}")
        email_notification(
            recipients=EMAIL_RECIPIENTS,
            contents="Exchange rate job failed; check logs",
        )
```

There are a lot of things to like here (at least I hope so; I'm the one who wrote it!). The code seems fairly modular, and it has function names that make sense. It also uses type hints, which can be enforced [using `mypy`](http://mypy-lang.org/). Suppose further that it has a test suite with full coverage that mocks out the API and database; the tests run via Jenkins; the code is deployed with a one-button deployment; and the job itself runs on a cron at UTC midnight but can be retried with a one-button click if needed. I would wager that this code quality and deployment process is better than most custom-built ETL pipelines at small/medium-sized companies.

Yet it is also the case that this ETL pipeline does not work well. The issues stem from violating the principle of retry-driven development: if something fails, the code cannot be retried safely and/or correctly. There are a few reasons:

### Lack of separation of environments

Suppose something in the ETL job breaks (as it inevitably will). We would like to tinker with and run the code locally in order to debug the issue. Is this safe to do? Actually, no. Unless we have a way to mock out the (Redshift) data warehouse locally, we risk writing to our (production) table. And, if the Redshift connection were mocked locally, we would not be able to test that the `append_data` function works properly. (Note that, since our test suite uses mocks, it also does not truly test the functionality of this method). What we need is a way to run a job locally that does not touch production data, but still touches Redshift.

One easy way to support this workflow is to create a development schema (let's say, `varun_dev`). (As an aside, `dbt` enforces [this same workflow](https://docs.getdbt.com/docs/building-a-dbt-project/building-models/using-custom-schemas#managing-environments)). Then, we can use a Python package (I prefer `dynaconf`, but there are others) that sets a configuration variable differently if the code is run in the "development" environment vs. the "production" one. The code would look something like this:

```python
from dynaconf import settings

# this will be, say, "raw" in production but "varun_dev" in development
SCHEMA_NAME = settings.HISTORICAL_EXCHANGE_RATES.SCHEMA_NAME

def main():
    ...
      # insert (append) it into our data warehouse table
    append_data(frame, db_conn=DB_CONN, table=TABLE_NAME, schema=SCHEMA_NAME)
```

 

A further advantage of this approach is that multiple developers can work on the same code without interfering with each other: each will have their own development schema.

### Issues with append

Another problem with the current code has to do with the "append_data" function. Suppose the implementation uses Redshift's `COPY` [command](https://docs.aws.amazon.com/redshift/latest/dg/r_COPY.html): we write our `pandas` dataframe to a temporary csv on S3, and then use `COPY` to load the data from that csv into our table, by appending it to the existing data.

If we retry our code, there is an issue: we don't know whether this will correct the previous failure, or if it will instead append the data *yet again* to the table. In other words, we could have two copies of the data for a particular day, or just one.

If, say, Alex leaves the company (☹️), and his email address is disabled, then our `email_notification` function might error out, but the data in Redshift will have been loaded correctly. If, on the other hand, the failure occurs because the API was down, then the data in Redshift has not already been loaded. This behavior means we cannot safely retry the ETL job.

At this point, you might ask: why can't we simply check whether the data was loaded? If it was, we don't have to retry the script; if it wasn't, we can retry the script. I would argue very strongly against this idea. Each of these manual checks adds a "tax" to the process of maintaining an ETL job. Perhaps this tax is bearable with a small number of ETL pipelines, but, as the data volume grows and the ETL complexity grows correspondingly, it will eat away at an analytics engineer's productive time. It would be nice if, having fixed the issue at 2 am, in advance of the morning's company metrics email, I could simply push the button to run the script and go to bed. If we feel that "one button" development processes, like CI and CD, are valuable, then "one button" ETL runs are valuable for the same reason.

There are at least two ways to fix the issue with "append" I diagnosed above. One is to avoid using append; the other is to modularize our tasks in the same way that we modularized our code. Let's go one-by-one.

**Upsert, don't append**

Upsertion is a process of updating a table wherein rows that "match" are UPdated, whereas rows that don't "match" are inSERTed (hence the name, "UPSERTion"). What does "match" mean in this context? We typically define a set of "primary keys" — the combination of these primary keys defines a unique id for the row/record. If we attempt to upsert a new row where the set of primary keys match another row already in the table, then that old row is replaced with the new one. If there is no match, a new row is inserted.

(As an aside, the [details](https://docs.getdbt.com/docs/building-a-dbt-project/building-models/configuring-incremental-models/#how-do-incremental-models-work-behind-the-scenes) of how this is implemented vary from database to database. One simple strategy is to delete records for which the keys match, and then insert the entire dataset afterwards. Some databases, like Snowflake, support this operation [more natively](https://docs.snowflake.com/en/sql-reference/sql/merge.html) though.)

How can we use upsertion to our advantage in this case? By inspecting our `historical_exchange_rates` table, we see that the combination of `date` and `currency_code` define a unique id. Each day we should have exactly one row per currency code. Let's take these two columns to be our primary keys. Suppose we retry the job on the same day. (The "on the same day" is an important caveat, and relaxing this restriction will be covered below.) Then, there are two possibilities:

1. Our previous job failed before the Redshift load step, in which case no data with `date = CURRENT_DATE` exist, so the upsertion process will find no matches. Therefore, the entire dataset resulting from the retry job will be inserted. In this case, that's what we want.
2. Our previous job failed after the Redshift load step, in which case data already exists with `date = CURRENT_DATE`. The upsertion process will find matches for all of those rows and replace each of them. Arguably this is wasteful (we're replacing old data with identical new data), but it is both safe and correct.

Therefore, replacing our "append" operation with an "upsert" one makes our job safe to retry, with the caveat mentioned before.

Note that upsertion is a more expensive process than appending: it involves deletes as well as inserts. It can [also](https://dataintensive.net/) violate certain database "serializability" guarantees if run concurrently with other processes that hit the same table. Therefore, it may not be appropriate for all cases. Furthermore, for (stateless) events, even the choice of primary keys is not obvious. Pipelines for event-based data typically have to be built with much more care to avoid dropping or duplicating events: i.e., to ensure "exactly once" delivery.

**Modularize our tasks**

A different approach is to "modularize" our tasks, not just our code. What does this mean? We generally to write our code to maximize reusability and obey the single responsibility principle: that each function should do only one thing: whether that is extracting the data (`retrieve_exchange_rates`), transforming the data (`transform_data`), loading the data (`append_data` or `upsert_data`), and sending the email (`email_notification`). However, we have not written our tasks the same way. There is a single script that is executed via cron: it does all of the things listed above in sequence. Because different behavior occurs depending on exactly where this task fails, the retry logic becomes confusing.

As an alternative, we can break up this single task into two or more tasks. For example:

Task 1: `retrieve_exchange_rates` → `transform_data` → `upsert_data`

Task 2: `email_notification` (depending on status of task 1)

Why is this better? If task 1 completes successfully, and the data is loaded into the data warehouse, there is no need to retry it. We can instead just fix the email bug and (optionally) retry that task. On the other hand, if task 1 does not complete successfully, we can retry the whole thing (task 1 + task 2) from the beginning.

You might notice that this is significantly more complicated than a simple cron job. We would like to chain together tasks in some order, store (statefully) the results of the tasks, and retry either from the beginning or partway through. What we are really after is a "workflow management" tool, of which [Apache Airflow](https://airflow.apache.org/) is the best-known. The core concept of such a tool is the "DAG": a graph that describes the tasks and the dependencies between them. (In this case, task 2 depends on task 1, but not the other way around.) If you are interested in learning more, this is the [original post](https://medium.com/airbnb-engineering/airflow-a-workflow-management-platform-46318b977fd8) (by the same Maxime Beauchemin!) that announced the open-sourcing of Airflow and shared its design principles.

You might also ask: why don't we modularize the tasks even further? If our ETL job does four separate things, why don't we have four separate tasks? This is possible and even desirable, but tricky. Remember that, according to our principle, each task needs to be able to be retried safely and correctly. How would we retry the `transform_data` task unless we saved the results of the extraction task (`retrieve_exchange_rates`) somewhere? Airflow does not natively support this behavior. There are two possibilities. One is to use a tool like [Prefect](https://medium.com/the-prefect-blog/why-not-airflow-4cfa423299c4), which supports the passing of data between tasks (N.B.: I have not used Prefect). The other is to implement data passing using (cloud) storage, such as S3. In other words, we might have tasks like this:

Task 1: retrieve exchange rates from API, write raw data to S3

Task 2: read raw data from S3, transform, write transformed data to S3

Task 3: load transformed data into db from S3

Task 4: email notification

Although this makes the code significantly more complicated, using S3 as an intermediate data store is actually a powerful paradigm that has non-obvious advantages, as we will see shortly.

(One final note on this topic: in production, I would recommend using both approaches: task modularization and upserting instead of appending. They do not interfere with one another and might even be seen as complementary.)

## Dealing with time

The final problem with our script is that it does not deal with time properly. I hope to clarify this (pseudo-profound) statement with some examples.

Suppose that we are not able to retry the script on the same day. What happens, even accounting for the improvements made previously (environment separation, upserting instead of appending, and modularizing our tasks)? Unfortunately, we're screwed. If we retry the script on a different day, then it will collect data for that day and populate the `historical_exchange_rates` table with it. But this does not solve the problem of the missing data from the day of interest. In other words, the retry is safe, but not correct.

(Careful readers might notice that even retrying the script on the same day can lead to subtle issues. If the data underlying the exchange rate API changes every hour, then retrying the script in the morning will not produce exactly the same exchange rate data as if it had run successfully at midnight UTC.)

We can make a final set of improvements to our script. The obvious one is to use the *historical* exchange rates API endpoint instead of the current one. This seems easy but in fact it is incompatible with the original cron scheduler. The issue has to do with the "logical date" vs the actual date. If we retry the Dec 1 job on Dec 3, we want the date passed to the API endpoint to be Dec 1, *not* Dec 3. In other words, each daily run of the ETL job has a "logical date" associated with it, and, even when retrying later, that logical date needs to be attached to that run. Using the actual date in place of the logical date leads to errors. While cron does not handle this issue, both Airflow and Prefect do.

For many data sources, historical data is not readily available. The example of exchange rates that forms the basis of the case study is perhaps too optimistic. Supporting historical data lookups adds extra work (and possibly also blows up the size of the database), and many APIs choose not to support it (or, at the very least, limit the amount of time that you can look back). How do we deal with time in these cases?

The solution is actually one we've already mentioned: modularizing the tasks! The key idea here is that, if we save the raw data first, then "downstream" tasks (tasks that occur after saving the raw data) can be retried both safely and correctly. Of course, if that first task fails, we're still borked, but at least errors in other parts of the code are not critical.

A final concept related to "dealing with time" is "schema evolution". It often happens that ETL jobs expand in scope. Our original exchange rate ETL job captured exchange rates in the base currency of USD. Suppose we want to be able to capture other base currencies as well. We can imagine evolving the schema by renaming the `exchange_rate_usd` column to `exchange_rate`, and adding a `base_currency_code` column, backfilled to be "USD" for all existing data.

While the migration of the table is straightforward, the changes for the ETL job might not be. One final application of the retry-driven development principle is that we want *historical* retries, even of successful tasks, to be safe and correct. This gives us some peace of mind: even if the `historical_exchange_rates` table gets dropped, for whatever reason, we can rebuild it. This requires writing the "transform" task in a careful way. We might have something like:

```python
def transform_data(raw_data_file: str) -> pd.DataFrame:
    [read_raw_data_from_s3]
    [original transformation code]

    frame = frame.rename(columns={"exchange_rate": "exchange_rate_usd"})
    if "base_currency_code" not in frame:
        frame["base_currency_code"] = "USD"

    [write_transformed_data_to_s3]
```

As you can see, this code handles the historical and current datasets properly, albeit at the cost of added complexity and extra test cases. If we did not modify it, historical retries could not be run safely or correctly. (There are better ways to handle schema evolution, and products like AWS Glue and Databricks that make this process much easier, but this code snippet was provided simply for the purpose of illustration.) 

## Summary and conclusions

What I like about this case study is that it is (deceptively) simple, yet it reveals many core concepts to data engineering and even data science. In my view, the principal conclusions are:

1. Be compassionate towards your future self. Your ETL code will undoubtedly break. Write your code so that it can be retried afterwards (i.e., after fixing it) both safely and correctly.
2. An added benefit of retry-driven development is that it forces you to break larger tasks down into smaller ones, in the same way that test-driven development forces you to write smaller, more testable functions. The result is "data flows" that are easier to reason about, not only because they are smaller but also because they can be safely retried without much thought.
3. Use environments to avoid touching production data. This is probably obvious to those with an engineering background, but perhaps not to some data scientists or other "hackers" who are [often recruited](https://multithreaded.stitchfix.com/blog/2016/03/16/engineers-shouldnt-write-etl/) to write ETL.
4. External data is often ephemeral. If it is not too costly, saving the raw data to a data store is a good way to be able to reconstruct the data of interest at a later date.
5. Thinking about time is surprisingly tricky! Try to walk through the possible retry scenarios (rerunning historical tasks, retrying failed tasks on the same day, and on a different day, etc.) before you write code, not after. Distinguishing between the logical data and actual date will help you reason about these cases.
6. At some point, cron won't be good enough. Proper workflow management tools add immeasurable value by making recovering from failure straightforward and efficient (only failed tasks need to be retried, not the entire job).
7. Upsertion is powerful way to write safe and correct data flows. It ensures that you never duplicate data, which is possible with append-based flows. Much more could be said about this topic, but I'd recommend reading the post by Maxime Beauchemin linked at the top to learn more about "functional data engineering" and "idemptotent" processes (of which upsertion is one).

Let me know your thoughts! Both the first draft and final draft of the ETL script are available on GitHub [TODO], although the Airflow code/DAG is omitted.
