SELECT 
LOWER("Username") as old_username
, LOWER(REPLACE(LOWER("Username"),'@qtsdatacenters.com','@q.com')) as new_username
, "Site_Role"
, "Last_Login"
FROM SANDBOX_TABLEAU_DB.TABLEAU_PROD_IL_DB.TAB_DIM_USER 
WHERE "Site_Role" != 'Unlicensed'
AND COLLATE(LOWER("Username"),'') ILIKE '%@qtsdatacenters.com'