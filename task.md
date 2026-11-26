Developer Trial Task: Booking.com Review Insights Dashboard
_This is your technical task. Take your time, ask good questions, and document your decisions. We assess how you think, not just what you build._

Objective
Build a working application that collects and analyses Booking.com guest reviews for four Azzurro Hotels properties. The application will be used by non-technical operations staff to understand guest sentiment, monitor property performance, and identify recurring operational issues.

Properties
Olympic Hotel Paddington — booking.com/hotel/au/olympic-paddington
Potts Point — booking.com/hotel/au/venus-potts-point-sydney
Central Sydney — booking.com/hotel/au/venus-surry-hills
Darling Harbour — booking.com/hotel/au/chateau-de-venus
Task requirements
1. Review collection
Booking.com does not provide us with API access. Develop a reliable alternative method for collecting publicly available reviews from the four property pages. The solution should:

Collect review text, rating, date, property, and other useful available information.
Avoid duplicate reviews.
Support updating the dataset with newly published reviews.
Handle page changes, errors, and temporary failures reasonably.
Document the approach, limitations, and reliability considerations.
2. Operations dashboard
Build a frontend that can be used easily by non-technical operations staff. The dashboard should include, at minimum:

Average rating for the current week.
Comparison against the previous week.
Property-by-property rating breakdown.
A review feed where users can browse and read individual reviews.
Date and property filters.
Positive and negative review trends.
3. Review insights
Create a way to group reviews into common operational topics, such as:

Cleanliness
Check-in experience
Staff or receptionist behaviour
Noise
Facilities
Location
Room condition
Value for money
Example insight: "40% of negative reviews this week mentioned cleanliness-related issues."

You may use keyword-based classification, an AI model, sentiment analysis, or another approach. Be prepared to explain your methodology and its limitations.

Technical freedom
You may use any programming language, framework, database, or hosting platform. We will assess the quality of your decisions rather than your choice of technology.

Deliverables
A GitHub repository or ZIP file containing the complete source code.
A README with setup instructions, how to run the scraper and frontend, an architecture overview, an explanation of the review collection method, and known limitations and assumptions.
A working local application or publicly accessible demo.
Sample review data collected from the listed properties.
Do not commit passwords, API keys, cookies, personal credentials, or other sensitive information.

Evaluation criteria
Reliability of the review collection process.
Code quality and project structure.
Accuracy and usefulness of the insights.
Dashboard usability for non-technical users.
Error handling and data quality controls.
Documentation and ease of setup.
Product thinking and understanding of the business problem.
Important note
This is an open-ended trial task. We are interested in how you approach unclear requirements, technical limitations, data reliability, and user experience. You may add additional features or KPIs where they provide meaningful value to the operations team.

How to complete it here
Post a short progress update in this app every 30–60 minutes while you work: what you finished, what you are doing next, any blockers. Keep everything in this app — not WhatsApp or Discord. Your update timeline is part of the assessment.
When you are done, submit below: attach your code (ZIP) and/or a repository link, plus a short note on your approach and trade-offs.

