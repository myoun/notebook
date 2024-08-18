from bs4 import BeautifulSoup
import requests, json, os
from typing import TypedDict
from neo4j import GraphDatabase, ManagedTransaction

URI = "bolt://localhost"
AUTH = ("neo4j", "saveplate")

class Recipe(TypedDict):
    name: str
    ingredients: list[tuple[str]]
    sauces: list[tuple[str]]
    recipe: list[str]

class Food(TypedDict):
    name: str
    recipes: list[Recipe]

def food_list() -> set[str]:
    main_list_url = "https://www.10000recipe.com/recipe/list.html"
    response = requests.get(main_list_url)

    if response.status_code == 200:
        html = response.text
        soup = BeautifulSoup(html, "html.parser")
    else:
        print("HTTP response error :", response.status_code)
        return
    
    category3 = soup.select_one("#id_search_category > table > tbody > tr:nth-child(1) > td > div > div:nth-child(3)")
    cat3items = category3.findChildren()[1:]
    ids = []
    for item in cat3items:
        ids.append(item['href'].split("'")[3])

    food_set = set()
    
    for i in ids:
        print(f"Getting food from {i}")
        sub_list_url = f"https://www.10000recipe.com/recipe/list.html?cat3={i}"
        response = requests.get(sub_list_url)

        if response.status_code == 200:
            html = response.text
            soup = BeautifulSoup(html, "html.parser")
        else:
            print("HTTP response error :", response.status_code)
            return
        
        food_container = soup.select("#contents_area_full > div.s_category_tag > ul > li")

        for food in food_container:
            food_set.add(food.text)
    print(food_set)
    return food_set
        

def food_info(name) -> Recipe:
    url = f"https://www.10000recipe.com/recipe/list.html?q={name}"
    response = requests.get(url)
    if response.status_code == 200:
        html = response.text
        soup = BeautifulSoup(html, 'html.parser')
    else : 
        print("HTTP response error :", response.status_code)
        return
    
    food: Food = { 'name' : name, 'recipes': [] }
    
    food_list = soup.find_all(attrs={'class':'common_sp_link'})
    for i in range(10):
        try:
            print(f"Crawling {i+1}/10 recipes.")
            food_id = food_list[i]['href'].split('/')[-1]
            new_url = f'https://www.10000recipe.com/recipe/{food_id}'
            new_response = requests.get(new_url)
            if new_response.status_code == 200:
                html = new_response.text
                soup = BeautifulSoup(html, 'html.parser')
            else : 
                print("HTTP response error :", response.status_code)
                return
            
            food_info = soup.find(attrs={'type':'application/ld+json'})
            result = json.loads(food_info.text)
            recipe_name = result['name']

            raw_ingredients = soup.select("#divConfirmedMaterialArea > ul:nth-child(1) > li")
            raw_sauces = soup.select("#divConfirmedMaterialArea > ul:nth-child(2) > li")

            ingredients = []

            for raw_ingrdient in raw_ingredients:
                ingredient = raw_ingrdient.select_one(".ingre_list_name > a").text.strip()
                quantity = raw_ingrdient.select_one(".ingre_list_ea").text.strip()
                ingredients.append((ingredient, quantity))

            sauces = []

            for raw_sauce in raw_sauces:
                sauce = raw_sauce.select_one(".ingre_list_name > a").text.strip()
                quantity = raw_sauce.select_one(".ingre_list_ea").text.strip()
                sauces.append((sauce, quantity))

            
            recipe = [result['recipeInstructions'][i]['text'] for i in range(len(result['recipeInstructions']))]
            for i in range(len(recipe)):
                recipe[i] = f'{i+1}. ' + recipe[i]
            
            res: Recipe = {
                'name': recipe_name,
                'ingredients': ingredients,
                'sauces': sauces,
                'recipe': recipe
            }
            food['recipes'].append(res)
        except:
            print("Something wrong...")

    return food


def add_new_food(tx: ManagedTransaction, food: Food):
    print(f"Creating Food Node")
    food_result = tx.run("""
        MERGE (food: Food {name: $name})
        RETURN elementId(food) as foodId;
    """, name=food["name"])
    print(f"Created Food Node")

    foodId = food_result.single()["foodId"]

    for recipe in food['recipes']:
        print(f"Adding Recipe {recipe['name']}...") 
        recipe_result = tx.run("""
            MATCH (food: Food) WHERE elementId(food) = $foodId
            MERGE (recipe: Recipe {name: $name, recipe: $recipe})
            MERGE (recipe)-[:RECIPE_OF]->(food)
            RETURN elementId(recipe) as recipeId;
        """, name=recipe['name'], recipe=recipe['recipe'], foodId=foodId)

        recipeId = recipe_result.single()["recipeId"]

        for ingredient in recipe['ingredients']:
            name, amount = ingredient
            name = name.strip()
            if amount == '':
                tx.run("""
                    MATCH (recipe: Recipe) WHERE elementId(recipe) = $recipeId
                    MERGE (ingredient:Ingredient{name: $name})
                    MERGE (ingredient)-[:INGREDIENT_OF]->(recipe)
                    RETURN recipe
                """, recipeId = recipeId, name = name)
            else:
                tx.run("""
                    MATCH (recipe: Recipe) WHERE elementId(recipe) = $recipeId
                    MERGE (ingredient:Ingredient{name: $name})
                    MERGE (ingredient)-[:INGREDIENT_OF{amount: $amount}]->(recipe)
                    RETURN recipe
                """, recipeId = recipeId, name = name, amount=amount)

        for sauce in recipe['sauces']:
            name, amount = sauce
            name = name.strip()
            if amount == '':
                tx.run("""
                    MATCH (recipe: Recipe) WHERE elementId(recipe) = $recipeId
                    MERGE (sauce:Sauce{name: $name})
                    MERGE (sauce) -[:SAUCE_OF]->(recipe)
                    RETURN recipe
                """, recipeId = recipeId, name = name)
            else:
                tx.run("""
                    MATCH (recipe: Recipe) WHERE elementId(recipe) = $recipeId
                    MERGE (sauce:Sauce{name: $name})
                    MERGE (sauce) -[:SAUCE_OF{amount: $amount}]->(recipe)
                    RETURN recipe
                """, recipeId = recipeId, name = name, amount=amount)

if (not os.path.isfile("crawling.json")):
    found_food = food_list()
    with open("crawling.json", "w", encoding="utf8") as f:
        json.dump({"progress": 0, "food": list(found_food)}, f)

with open("crawling.json", "r", encoding="utf8") as f:
    food_json = json.load(f)
    progress = food_json["progress"]
    food_set = food_json["food"][progress:]

with GraphDatabase.driver(URI, auth=AUTH) as driver:
    driver.verify_connectivity()
    with driver.session(database="neo4j") as session:
        for i, food in enumerate(food_set):
            print(f"Adding recipes for {food} ({food_json["progress"]+1}/{len(food_set)})")
            recipe = food_info(food)
            session.execute_write(add_new_food, recipe)

            food_json["progress"] += 1
            with open("crawling.json", "w", encoding="utf8") as f:
                json.dump(food_json, f)