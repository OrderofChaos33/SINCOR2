from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, SubmitField
from wtforms.validators import DataRequired, Email, Optional, Length

class QuoteForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=120)])
    email = StringField("Email", validators=[Optional(), Email(), Length(max=120)])
    phone = StringField("Phone", validators=[Optional(), Length(max=50)])
    vehicle = StringField("Vehicle", validators=[Optional(), Length(max=120)])
    service = StringField("Service", validators=[Optional(), Length(max=120)])
    message = TextAreaField("Message", validators=[Optional(), Length(max=2000)])
    submit = SubmitField("Request Quote")
