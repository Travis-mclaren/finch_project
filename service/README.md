
# Django Starter Code

## Setup Instructions

1. **Clone the Repository**

   ```bash
   git clone https://github.com/CaseQuill/django-starter.git
   cd django-starter
   ```

2. **Install Poetry**

   Ensure that Poetry is installed on your system. If not, install it using:

   ```bash
   curl -sSL https://install.python-poetry.org | python3 -
   ```

   For more installation options, visit the [Poetry documentation](https://python-poetry.org/docs/#installation).

3. **Install Project Dependencies**

   ```bash
   poetry install
   ```

   This command will:

   - Create a virtual environment (if one doesn't exist).
   - Install all dependencies specified in `pyproject.toml`.

4. **Make and Apply Migrations**

   ```bash
   poetry run python manage.py makemigrations
   poetry run python manage.py migrate
   ```

5. **Create a Superuser (Optional)**

   If you want to use the admin interface to add items:

   ```bash
   poetry run python manage.py createsuperuser
   ```

6. **Run the Development Server**

   ```bash
   poetry run python manage.py runserver
   ```

7. **Access the Application**

   - **Item List View**: [http://localhost:8000/items/](http://localhost:8000/items/)
   - **Admin Interface**: [http://localhost:8000/admin/](http://localhost:8000/admin/)

---

## Adding Sample Data

You can add sample data in two ways:

### Using the Admin Interface

1. Navigate to [http://localhost:8000/admin/](http://localhost:8000/admin/).
2. Log in with your superuser credentials.
3. Click on **Items** and add new items.

### Using the Django Shell

```bash
poetry run python manage.py shell
```

Then, execute:

```python
from core.models import Item
Item.objects.create(name='Sample Item 1')
Item.objects.create(name='Sample Item 2')
exit()
```

---

## Alternative: Using the Poetry Shell

You can activate the Poetry shell to avoid prefixing commands with `poetry run`:

```bash
poetry shell
```

Now you can run Django commands directly:

```bash
python manage.py migrate
python manage.py runserver
```

---

## Notes

- Ensure you have **Python 3.6** or higher installed.
- If you encounter any issues, please check your Python and Poetry installations.
- For any questions or issues, feel free to contact us at [ben@finchlegal.com](mailto:ben@finchlegal.com).

---

## Brief Overview

This starter project includes:

- A Django project set up with Poetry for dependency management.
- A `core` app with a simple `Item` model.
- A basic view (`item_list`) that displays a list of `Item` instances.
- Templates configured to render the `item_list.html`.
- URLs configured to route to the `item_list` view.
- Instructions to help you get started quickly.



## How to run

1. Run ```python manage.py runserver ``` to start server
2. Make an API call with the transcript to parse
<b>POST</b>
http://127.0.0.1:8000/api/v1/communications/ingest/
```json
{
   "id": "123",
   "transcript": [
      {
         "speaker": "Intake Specialist",
         "text": "Hello my name is..."
      },
      {
         "speaker": "Caller",
         "text": "Yes, I have been injured..."
      },
      ...
   ]
}
```
3. Wait for it to finish
4. Grab case ID from response
5. In a browser, hit  
http://localhost:8000/api/v1/cases/{CaseId}/summary/
